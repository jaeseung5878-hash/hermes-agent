"""
Claude A2A 브라우저 자동화
- Playwright로 claude.ai 웹 UI 조작
- 프롬프트 전송 → 스트리밍 완료 대기 → 응답 파싱
- Cloudflare 봇 감지 우회를 위한 stealth 패치 포함
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from .session import A2AError, inject_session

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Locator, Page

logger = logging.getLogger(__name__)

CLAUDE_ROOT_URL = "https://claude.ai/"
CLAUDE_URL = "https://claude.ai/new"
RESPONSE_TIMEOUT_MS = 180_000  # 3분 (긴 응답/웹검색 대비)
COMPOSER_WAIT_MS = 120_000     # 2분 (Cloudflare Turnstile이 90초 이상 걸리기도 함)

# claude.ai의 DOM은 자주 바뀌므로 fallback 체인으로 시도한다.
# 가장 안정적인 것은 contenteditable + role=textbox 조합.
TYPING_SELECTORS: List[str] = [
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"].ProseMirror',
    'div.ProseMirror[contenteditable="true"]',
    '[data-testid="message-input"]',
    '[data-testid="chat-input"]',
    'textarea[placeholder*="Claude"]',
    'textarea[placeholder*="Reply"]',
]

SEND_SELECTORS: List[str] = [
    'button[aria-label="Send message"]',
    'button[aria-label="Send Message"]',
    'button[data-testid="send-button"]',
    'button[type="submit"][aria-label*="end"]',
]

STOP_SELECTORS: List[str] = [
    'button[aria-label="Stop response"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="Stop generating"]',
    '[data-testid="stop-button"]',
]

RESPONSE_CONTAINER_SELECTORS: List[str] = [
    '[data-testid="assistant-message"]',
    '.font-claude-message',
    '[data-testid="message"] .prose',
    '.prose',
]

# Railway 볼륨(/data)이 있으면 에러 스크린샷은 그쪽에 저장.
_DEBUG_DIR = Path("/data") if Path("/data").exists() else Path.home() / ".hermes"
_PROMPT_MAX_CHARS = int(os.environ.get("A2A_PROMPT_MAX_CHARS", "8000"))

# Chromium launch 인자 — 자동화 감지 플래그 비활성화.
# WHY no --disable-gpu: STEALTH_INIT_SCRIPT의 WebGL vendor 스푸핑이 동작하려면
# GPU 파이프라인이 살아있어야 함. 끄면 fingerprint가 'SwiftShader'로 잡혀
# Cloudflare JS 챌린지에서 headless로 분류됨.
_STEALTH_ARGS: List[str] = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",
]

# navigator.webdriver, permissions API, chrome object 등을 패치해서 headless 티를
#줄인다. Cloudflare JS challenge의 1차 방어막을 대부분 통과시킨다.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({}))
});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
}
// WebGL vendor spoof
try {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, p);
    };
} catch (e) {}
"""


def _truncate_prompt(prompt: str, max_chars: int = _PROMPT_MAX_CHARS) -> str:
    """claude.ai 입력창에 타이핑 가능한 수준으로 프롬프트 길이 제한."""
    if len(prompt) <= max_chars:
        return prompt
    logger.warning(
        "Prompt truncated: %d → %d chars (A2A_PROMPT_MAX_CHARS=%d)",
        len(prompt), max_chars, max_chars,
    )
    head = max_chars // 3
    tail = max_chars - head - 30
    return prompt[:head] + "\n\n[…TRUNCATED…]\n\n" + prompt[-tail:]


async def _first_visible(page: "Page", selectors: List[str], timeout_ms: int) -> "Locator":
    """Fallback 체인으로 첫 번째 보이는 요소를 반환. 모두 실패하면 A2AError."""
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    last_error: Optional[Exception] = None

    while asyncio.get_running_loop().time() < deadline:
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.count() > 0 and await locator.is_visible():
                    return locator
            except Exception as e:
                last_error = e
        await asyncio.sleep(0.5)

    raise A2AError(
        f"모든 selector 실패 ({len(selectors)}개 시도). 마지막 에러: {last_error}"
    )


async def _save_debug_screenshot(page: "Page", reason: str) -> None:
    """현재 페이지 스크린샷을 _DEBUG_DIR에 저장 (실패 디버깅용)."""
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        path = _DEBUG_DIR / f"a2a_error_{reason}_{int(time.time())}.png"
        await page.screenshot(path=str(path), full_page=True)
        logger.info("디버그 스크린샷 저장: %s", path)
    except Exception as e:
        logger.warning("스크린샷 저장 실패: %s", e)


class ClaudeA2ABrowser:
    """claude.ai Playwright 세션 래퍼."""

    def __init__(self, browser: "Browser") -> None:
        self._browser = browser
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    async def __aenter__(self) -> "ClaudeA2ABrowser":
        self._context = await self._browser.new_context(
            viewport={"width": 1366, "height": 900},
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        await self._context.add_init_script(_STEALTH_INIT_SCRIPT)
        await inject_session(self._context)
        self._page = await self._context.new_page()
        return self

    async def __aexit__(self, *_) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()

    async def ask(self, prompt: str) -> str:
        """claude.ai에 프롬프트를 전송하고 최종 응답 텍스트를 반환."""
        if self._page is None:
            raise A2AError("브라우저 컨텍스트가 초기화되지 않았습니다.")

        prompt = _truncate_prompt(prompt)

        try:
            await self._navigate_to_composer()
            composer = await _first_visible(self._page, TYPING_SELECTORS, timeout_ms=15_000)
        except Exception as e:
            await _save_debug_screenshot(self._page, "composer_not_found")
            raise A2AError(f"composer 찾기 실패: {e}") from e

        await composer.click()
        await composer.fill(prompt)

        try:
            send_btn = await _first_visible(self._page, SEND_SELECTORS, timeout_ms=5_000)
            await send_btn.click()
        except A2AError:
            # Fallback: Enter 키 전송
            logger.info("Send 버튼 못 찾음 — Enter 키로 대체")
            await composer.press("Enter")

        # 스트리밍 완료 대기 — Stop 버튼이 나타났다가 사라지면 완료.
        await self._wait_for_response_complete()

        response = await self._parse_response()
        logger.info("A2A 응답 수신 (%d자)", len(response))
        return response

    async def _navigate_to_composer(self) -> None:
        """claude.ai 세션 확보 → composer 도달.

        전략:
          1) `/new` 직행은 CF가 high-risk endpoint로 간주해 challenge_redirect
             를 걸 확률이 높다. 루트 `/` 로 먼저 진입해 세션 쿠키가 passive
             challenge를 통과하게 한다.
          2) `wait_until="networkidle"` 로 CF Turnstile JS가 끝나길 기다린다.
          3) `wait_for_url` predicate로 challenge/login 이 아닌 앱 URL에 안착
             할 때까지 폴링 없이 대기.
          4) 안착 후 composer 선택자 체인 탐색.
        """
        assert self._page is not None

        # 1) 루트 진입. claude.ai는 세션 쿠키가 유효하면 /chats 또는 /new로
        # 내부 리다이렉트시키는데, 이 경로는 CF challenge_redirect를 덜 튀게 한다.
        # WHY domcontentloaded (not networkidle): claude.ai는 SPA라 websocket+
        # 폴링이 상시 돌아 networkidle에 절대 도달하지 못한다. DOM만 뜨면
        # wait_for_url이 URL 전환을 감지해준다.
        await self._page.goto(
            CLAUDE_ROOT_URL, wait_until="domcontentloaded", timeout=60_000,
        )

        logger.info("루트 진입 후 URL: %s", self._page.url)

        # 2) challenge/login 이 아닌 URL까지 대기. CF Turnstile이 알아서 통과
        # 시키면 URL이 바뀐다. predicate가 True가 되는 순간 리턴.
        def _is_app_url(url: str) -> bool:
            lower = url.lower()
            if "/login" in lower:
                return False
            if "challenge" in lower:
                return False
            if "/api/" in lower:
                return False
            return lower.startswith("https://claude.ai/")

        try:
            await self._page.wait_for_url(
                _is_app_url, timeout=COMPOSER_WAIT_MS,
            )
        except Exception as e:
            # 현재 페이지가 /login이면 쿠키 문제로 간주.
            current = self._page.url
            if "/login" in current.lower():
                await _save_debug_screenshot(self._page, "login_redirect")
                raise A2AError(
                    f"/login으로 리다이렉트됨 ({current}) — 쿠키 만료/무효. "
                    "scripts/login_a2a.py로 재로그인 후 CLAUDE_A2A_COOKIES 갱신하세요."
                ) from e
            # 아니면 CF 챌린지에 막힌 것. 페이지 내용 스니펫을 로그로 남겨
            # 원격 진단 가능하도록 한다.
            await _save_debug_screenshot(self._page, "challenge_stuck")
            try:
                html = await self._page.content()
                title = await self._page.title()
            except Exception:
                html = ""
                title = "<unavailable>"
            logger.error(
                "Cloudflare 챌린지 탈출 실패. URL=%s title=%r html[:400]=%s",
                current, title, html[:400].replace("\n", " "),
            )
            raise A2AError(
                f"{COMPOSER_WAIT_MS/1000:.0f}초 내에 CF 챌린지 탈출 실패. "
                f"URL: {current}. Railway IP가 CF에 플래그됐거나 headless "
                f"fingerprint 감지됐을 가능성. 디버그 스크린샷을 /data에서 확인."
            ) from e

        # 3) 이제 앱 URL — /new 가 아니면 명시적으로 이동. 같은 origin + 세션
        # 살아있으므로 재챌린지 확률 낮음.
        if "/new" not in self._page.url:
            logger.info("앱 URL 안착 (%s) — /new로 이동", self._page.url)
            await self._page.goto(
                CLAUDE_URL, wait_until="domcontentloaded", timeout=30_000,
            )

        # 4) composer 선택자 체인은 ask() 쪽에서 _first_visible로 처리.
        # 여기선 최소 하나가 attached 되었는지만 확인 (빠른 fail).
        try:
            await self._page.wait_for_selector(
                ", ".join(TYPING_SELECTORS), state="attached", timeout=15_000,
            )
        except Exception as e:
            await _save_debug_screenshot(self._page, "composer_never_attached")
            raise A2AError(
                f"composer가 /new 로드 후에도 DOM에 나타나지 않음. URL: {self._page.url}"
            ) from e

    async def _wait_for_response_complete(self) -> None:
        """Stop 버튼 등장→소멸로 스트리밍 완료를 감지. 실패해도 계속 진행."""
        assert self._page is not None
        try:
            stop_selector = ", ".join(STOP_SELECTORS)
            await self._page.wait_for_selector(stop_selector, timeout=15_000)
            await self._page.wait_for_selector(
                stop_selector, state="hidden", timeout=RESPONSE_TIMEOUT_MS,
            )
        except Exception as e:
            logger.warning("스트리밍 대기 중 예외 (계속 진행): %s", e)
            # 안전망: 2초 더 기다려서 마지막 토큰이 붙도록 함
            await asyncio.sleep(2)

    async def _parse_response(self) -> str:
        """페이지에서 마지막 AI 응답 텍스트를 추출."""
        assert self._page is not None
        last_error: Optional[Exception] = None
        for sel in RESPONSE_CONTAINER_SELECTORS:
            try:
                elements = await self._page.locator(sel).all()
                if elements:
                    text = (await elements[-1].inner_text()).strip()
                    if text:
                        return text
            except Exception as e:
                last_error = e
        await _save_debug_screenshot(self._page, "response_parse_failed")
        raise A2AError(
            f"응답 요소를 찾지 못했습니다. 마지막 에러: {last_error}"
        )


async def run_a2a(prompt: str) -> str:
    """A2A 세션을 열고 응답을 반환하는 비동기 편의 함수."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise A2AError(
            "playwright 패키지 미설치. `pip install playwright && playwright install chromium`"
        ) from e

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=_STEALTH_ARGS,
        )
        try:
            async with ClaudeA2ABrowser(browser) as client:
                return await client.ask(prompt)
        finally:
            await browser.close()


def run_a2a_sync(prompt: str) -> str:
    """동기 래퍼 — 기존 event loop가 없을 때 사용."""
    return asyncio.run(run_a2a(prompt))

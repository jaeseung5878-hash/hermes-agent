"""
Claude A2A 브라우저 자동화
- Playwright로 claude.ai 웹 UI 조작
- 프롬프트 전송 → 스트리밍 완료 대기 → 응답 파싱
"""
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

from .session import A2AError, inject_session, extract_session

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

CLAUDE_URL = "https://claude.ai/new"
RESPONSE_TIMEOUT_MS = 120_000  # 2분
TYPING_SELECTOR = '[data-testid="message-input"]'
SEND_SELECTOR = '[aria-label="Send message"]'
LOADING_SELECTOR = '[aria-label="Stop streaming"]'


class ClaudeA2ABrowser:
    """claude.ai Playwright 세션 래퍼."""

    def __init__(self, browser: "Browser") -> None:
        self._browser = browser
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    async def __aenter__(self) -> "ClaudeA2ABrowser":
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
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

        await self._page.goto(CLAUDE_URL, wait_until="domcontentloaded")
        await self._page.wait_for_selector(TYPING_SELECTOR, timeout=30_000)

        textarea = self._page.locator(TYPING_SELECTOR)
        await textarea.click()
        await textarea.fill(prompt)

        send_btn = self._page.locator(SEND_SELECTOR)
        await send_btn.click()

        # 스트리밍 시작 확인 후 완료까지 대기
        try:
            await self._page.wait_for_selector(LOADING_SELECTOR, timeout=15_000)
            await self._page.wait_for_selector(
                LOADING_SELECTOR,
                state="hidden",
                timeout=RESPONSE_TIMEOUT_MS,
            )
        except Exception as e:
            logger.warning("스트리밍 대기 중 예외 (계속 진행): %s", e)

        response = await self._parse_response()
        logger.info("A2A 응답 수신 (%d자)", len(response))
        return response

    async def _parse_response(self) -> str:
        """페이지에서 마지막 AI 응답 텍스트를 추출."""
        if self._page is None:
            raise A2AError("페이지가 초기화되지 않았습니다.")
        try:
            elements = await self._page.locator(".prose").all()
            if not elements:
                raise A2AError("응답 요소(.prose)를 찾을 수 없습니다.")
            return (await elements[-1].inner_text()).strip()
        except A2AError:
            raise
        except Exception as e:
            raise A2AError(f"응답 파싱 실패: {e}") from e


async def run_a2a(prompt: str) -> str:
    """A2A 세션을 열고 응답을 반환하는 비동기 편의 함수."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise A2AError(
            "playwright 패키지 미설치. `pip install playwright && playwright install chromium`"
        ) from e

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            async with ClaudeA2ABrowser(browser) as client:
                return await client.ask(prompt)
        finally:
            await browser.close()


def run_a2a_sync(prompt: str) -> str:
    """동기 래퍼 — 기존 event loop가 없을 때 사용."""
    return asyncio.run(run_a2a(prompt))

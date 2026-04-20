"""
Claude A2A login helper.

Runs Playwright Chromium in *headed* mode so you can log in to claude.ai
manually (Google / email magic link / whatever). Once the `/new` URL is
reachable without a redirect to `/login`, the cookies are extracted, saved to
``$HERMES_HOME/claude_a2a_session.json`` and also printed as a base64 string
suitable for the Railway ``CLAUDE_A2A_COOKIES`` environment variable.

Run locally (Windows/macOS/Linux with a display):
    python -m pip install playwright
    python -m playwright install chromium
    python scripts/login_a2a.py

The script will:
    1. Open a Chromium window pointed at https://claude.ai/login
    2. Wait while you log in (up to 10 minutes by default)
    3. Verify the session by loading /new and checking for the composer
    4. Write cookies to disk and emit the base64 one-liner for Railway
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Put the repo root on sys.path so `from skills.claude_a2a ...` works when
# invoked as `python scripts/login_a2a.py`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from skills.claude_a2a.session import (  # noqa: E402
    A2AError,
    encode_for_env,
    extract_session,
    save_session,
)

logger = logging.getLogger("login_a2a")

LOGIN_URL = "https://claude.ai/login"
VERIFY_URL = "https://claude.ai/new"
COMPOSER_SELECTOR = '[data-testid="message-input"]'
DEFAULT_WAIT_MINUTES = 10


async def _wait_for_login(page, timeout_ms: int) -> None:
    """Poll until we can reach /new and the composer is visible."""
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    while True:
        await page.goto(VERIFY_URL, wait_until="domcontentloaded")
        try:
            await page.wait_for_selector(COMPOSER_SELECTOR, timeout=5_000)
            return  # Logged in — composer is visible.
        except Exception:
            pass

        # Not logged in yet — check if we were redirected to /login and keep
        # waiting. The user is expected to complete login in the visible window.
        if asyncio.get_running_loop().time() > deadline:
            raise A2AError(
                f"로그인 대기 시간 초과({timeout_ms/1000:.0f}초). 다시 시도하세요."
            )
        print("… 로그인 기다리는 중. Chromium 창에서 로그인을 완료해주세요.")
        await asyncio.sleep(8)


async def _run(wait_minutes: int) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "playwright가 설치돼 있지 않습니다.\n"
            "    pip install playwright\n"
            "    python -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    print("=" * 60)
    print("Claude A2A 로그인")
    print("=" * 60)
    print(
        "Chromium 창이 열립니다. claude.ai에 로그인한 뒤 /new 페이지가\n"
        "보이도록 해주세요. 창을 닫지 말고 그대로 두면 자동으로 쿠키를\n"
        "추출합니다.\n"
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        timeout_ms = wait_minutes * 60 * 1000
        try:
            await _wait_for_login(page, timeout_ms)
        except A2AError as e:
            print(f"\n[실패] {e}", file=sys.stderr)
            await browser.close()
            return 1

        print("\n[성공] 로그인 감지됨. 쿠키 추출 중…")
        cookies = await context.cookies()
        if not cookies:
            print("[실패] 쿠키가 비어 있습니다.", file=sys.stderr)
            await browser.close()
            return 1

        save_session(cookies)
        b64 = encode_for_env(cookies)
        await browser.close()

    print("\n" + "=" * 60)
    print(f"쿠키 {len(cookies)}개 저장 완료")
    print("=" * 60)
    print("\n[1] 로컬 파일에 저장됨:")
    print(f"    {Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')).resolve()}/claude_a2a_session.json")
    print("\n[2] Railway Variables에 아래 값을 그대로 복사/붙여넣기:\n")
    print(f"CLAUDE_A2A_COOKIES={b64}\n")
    print(
        "[3] Railway에 저장 후 자동 재배포됩니다.\n"
        "    배포 완료 후 Slack에서 `!opus 안녕` 같은 메시지로 A2A 경로를 테스트하세요.\n"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude A2A 로그인 헬퍼")
    parser.add_argument(
        "--wait-minutes",
        type=int,
        default=DEFAULT_WAIT_MINUTES,
        help=f"로그인 대기 시간(분). 기본 {DEFAULT_WAIT_MINUTES}분.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(_run(args.wait_minutes))


if __name__ == "__main__":
    sys.exit(main())

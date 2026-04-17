"""
Claude A2A 세션 관리
우선순위: 환경변수 CLAUDE_A2A_COOKIES (base64 JSON) → 로컬 파일
Railway 같은 ephemeral 환경에서는 환경변수를 사용한다.
"""
from __future__ import annotations
import base64
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

SESSION_FILE = Path.home() / ".hermes" / "claude_a2a_session.json"
_ENV_KEY = "CLAUDE_A2A_COOKIES"


class A2AError(Exception):
    """Claude A2A 브라우저 자동화 실패."""


# ── 로드 ──────────────────────────────────────────────────────────────────────

def load_session() -> list[dict]:
    """저장된 쿠키 목록을 반환. 환경변수 우선, 없으면 파일에서 로드."""
    cookies = _load_from_env() or _load_from_file()
    if not cookies:
        raise A2AError(
            "세션 없음. `make login-a2a` 실행 후 "
            f"출력된 CLAUDE_A2A_COOKIES 값을 환경변수에 설정하세요."
        )
    logger.info("세션 로드 완료 (%d개 쿠키, 출처=%s)", len(cookies),
                "env" if _load_from_env() else "file")
    return cookies


def _load_from_env() -> list[dict] | None:
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        data = json.loads(decoded)
        cookies: list[dict] = data if isinstance(data, list) else data.get("cookies", [])
        return cookies or None
    except Exception as e:
        logger.warning("환경변수 %s 파싱 실패: %s", _ENV_KEY, e)
        return None


def _load_from_file() -> list[dict] | None:
    if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size <= 10:
        return None
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        cookies: list[dict] = data.get("cookies", [])
        return cookies or None
    except Exception as e:
        logger.warning("세션 파일 파싱 실패: %s", e)
        return None


def session_exists() -> bool:
    return bool(_load_from_env() or _load_from_file())


# ── 저장 ──────────────────────────────────────────────────────────────────────

def save_session(cookies: list[dict]) -> None:
    """쿠키를 로컬 파일에 저장하고 Railway용 base64 값을 반환."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps({"cookies": cookies}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("세션 파일 저장 (%d개 쿠키) → %s", len(cookies), SESSION_FILE)


def encode_for_env(cookies: list[dict]) -> str:
    """쿠키 목록을 Railway 환경변수용 base64 문자열로 인코딩."""
    raw = json.dumps(cookies, ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def delete_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        logger.info("세션 파일 삭제됨")


# ── Playwright 연동 ───────────────────────────────────────────────────────────

async def inject_session(context: "BrowserContext") -> None:
    """Playwright context에 저장된 쿠키를 주입."""
    cookies = load_session()
    await context.add_cookies(cookies)
    logger.info("세션 쿠키 Playwright context에 주입 완료")


async def extract_session(context: "BrowserContext") -> list[dict]:
    """현재 Playwright context에서 쿠키를 추출하여 저장하고 목록을 반환."""
    cookies = await context.cookies()
    if not cookies:
        raise A2AError("추출할 쿠키가 없습니다. 로그인 상태를 확인하세요.")
    save_session(cookies)
    return cookies

"""
Claude A2A 세션 관리
우선순위: 환경변수 CLAUDE_A2A_COOKIES (base64 JSON) → 로컬 파일
Railway 같은 ephemeral 환경에서는 환경변수를 사용한다.

쿠키 JSON 포맷은 다음을 모두 허용한다 (자동 변환):
- Playwright 네이티브 (name/value/domain/expires/sameSite: Lax)
- Cookie-Editor / EditThisCookie Chrome 확장 내보내기 (expirationDate,
  hostOnly, session, storeId 같은 여분 필드 + sameSite: lax 소문자)
- {"cookies": [...]} 래핑 형태

덕분에 사용자는 브라우저 확장으로 내보낸 JSON을 그대로 base64 인코딩해서
Railway에 붙여넣을 수 있다.
"""
from __future__ import annotations
import base64
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, List

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

logger = logging.getLogger(__name__)

# Railway 볼륨(/data) 우선, 없으면 홈 디렉토리
_DATA_DIR = Path("/data") if Path("/data").exists() else Path.home() / ".hermes"
SESSION_FILE = _DATA_DIR / "claude_a2a_session.json"
_ENV_KEY = "CLAUDE_A2A_COOKIES"


class A2AError(Exception):
    """Claude A2A 브라우저 자동화 실패."""


# ── 쿠키 정규화 ──────────────────────────────────────────────────────────────

_SAME_SITE_MAP = {
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "Lax",
}

_PLAYWRIGHT_ALLOWED_KEYS = {
    "name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite",
}


def _normalize_cookie(raw: dict) -> dict | None:
    """Convert an arbitrary browser-extension cookie dict into Playwright shape.

    Returns ``None`` if the cookie is unusable (missing name or value).
    """
    name = raw.get("name")
    value = raw.get("value")
    if not name or value is None:
        return None

    cookie: dict[str, Any] = {"name": name, "value": str(value)}

    domain = raw.get("domain")
    if domain:
        cookie["domain"] = domain
    cookie["path"] = raw.get("path") or "/"

    # Cookie-Editor uses expirationDate; Playwright wants expires (int seconds).
    exp = raw.get("expires")
    if exp is None:
        exp = raw.get("expirationDate")
    if exp is not None:
        try:
            cookie["expires"] = int(float(exp))
        except (TypeError, ValueError):
            pass

    if "httpOnly" in raw:
        cookie["httpOnly"] = bool(raw["httpOnly"])
    if "secure" in raw:
        cookie["secure"] = bool(raw["secure"])

    same_site = raw.get("sameSite")
    if same_site:
        mapped = _SAME_SITE_MAP.get(str(same_site).strip().lower())
        if mapped:
            cookie["sameSite"] = mapped

    # sameSite=None requires secure=true per spec; many exporters forget this.
    if cookie.get("sameSite") == "None":
        cookie["secure"] = True

    # Drop anything else — Playwright rejects unknown keys.
    return {k: v for k, v in cookie.items() if k in _PLAYWRIGHT_ALLOWED_KEYS}


def _normalize_cookies(items: Iterable[Any]) -> List[dict]:
    normalized: List[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        converted = _normalize_cookie(raw)
        if converted:
            normalized.append(converted)
    return normalized


def _unwrap_cookie_payload(data: Any) -> list:
    """Accept either a raw list or a ``{"cookies": [...]}`` wrapper."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        maybe = data.get("cookies")
        if isinstance(maybe, list):
            return maybe
    return []


# ── 로드 ──────────────────────────────────────────────────────────────────────

def load_session() -> list[dict]:
    """저장된 쿠키 목록을 반환. 환경변수 우선, 없으면 파일에서 로드.

    Returned cookies are always in Playwright-ready shape (name/value/domain/
    path/expires/httpOnly/secure/sameSite), normalized from whatever format
    the source supplied.
    """
    cookies_env = _load_from_env()
    cookies_file = None if cookies_env else _load_from_file()
    cookies = cookies_env or cookies_file
    if not cookies:
        raise A2AError(
            "세션 없음. 로컬에서 scripts/login_a2a.py를 실행하거나, Chrome 확장으로 "
            "claude.ai 쿠키를 내보내 base64로 인코딩한 뒤 CLAUDE_A2A_COOKIES 환경변수에 "
            "설정하세요."
        )
    source = "env" if cookies_env else "file"
    logger.info("세션 로드 완료 (%d개 쿠키, 출처=%s)", len(cookies), source)
    return cookies


def _load_from_env() -> list[dict] | None:
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:
        logger.warning("환경변수 %s 파싱 실패: %s", _ENV_KEY, e)
        return None
    cookies = _normalize_cookies(_unwrap_cookie_payload(data))
    return cookies or None


def _load_from_file() -> list[dict] | None:
    if not SESSION_FILE.exists() or SESSION_FILE.stat().st_size <= 10:
        return None
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("세션 파일 파싱 실패: %s", e)
        return None
    cookies = _normalize_cookies(_unwrap_cookie_payload(data))
    return cookies or None


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

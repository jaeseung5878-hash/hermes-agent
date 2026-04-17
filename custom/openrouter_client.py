"""
OpenRouter API 클라이언트
- httpx 비동기 + 동기 래퍼 제공
- classifier.py의 openrouter_call 시그니처 호환
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any

import httpx

from .models import Model
from .errors import RouterError

logger = logging.getLogger(__name__)

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 60.0


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RouterError("OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다.")
    return key


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/hermes-agent",
        "X-Title": "Hermes Smart Router",
    }


async def acall(
    model_id: str,
    prompt: str,
    max_tokens: int = 2048,
    system: str | None = None,
) -> str:
    """OpenRouter 비동기 채팅 완성 호출.

    Args:
        model_id: custom/models.py에 정의된 모델 ID.
        prompt: 사용자 메시지.
        max_tokens: 최대 출력 토큰 수.
        system: 시스템 프롬프트 (선택).

    Returns:
        모델 응답 텍스트.

    Raises:
        RouterError: API 호출 실패 또는 응답 파싱 실패 시.
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{OPENROUTER_API_BASE}/chat/completions",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RouterError(
                f"OpenRouter API 오류 {e.response.status_code}: {e.response.text}"
            ) from e
        except httpx.RequestError as e:
            raise RouterError(f"OpenRouter 연결 실패: {e}") from e

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RouterError(f"응답 파싱 실패: {data}") from e


def call(
    model_id: str,
    prompt: str,
    max_tokens: int = 2048,
    system: str | None = None,
) -> str:
    """동기 래퍼 — classifier.py의 openrouter_call 시그니처 호환."""
    return asyncio.run(acall(model_id, prompt, max_tokens, system))


def make_openrouter_call():
    """classifier.classify()에 넘길 openrouter_call 콜백을 반환."""
    def _call(model_id: str, prompt: str, max_tokens: int) -> str:
        return call(model_id, prompt, max_tokens)
    return _call

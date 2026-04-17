"""
Hermes Bridge
- Slack 메시지 → router.route() → 모델 실행 → 응답 반환
- A2A / OpenRouter 분기 처리
- 모든 라우팅 결정은 router.py에 위임
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from .router import route, RouteDecision
from .errors import RouterError
from .openrouter_client import acall, make_openrouter_call
from .models import CLAUDE_A2A, GPT_5_4
from .slack_ui import build_response_blocks, build_alert_blocks

logger = logging.getLogger(__name__)


@dataclass
class BridgeResult:
    answer: str
    blocks: list[dict]
    decision: RouteDecision


async def handle_message(text: str, forced_model_id: str | None = None) -> BridgeResult:
    """Slack 메시지를 받아 라우팅 후 응답을 반환.

    Args:
        text: 사용자 메시지.
        forced_model_id: Slack 버튼으로 수동 선택된 모델 ID (None이면 자동 라우팅).

    Returns:
        BridgeResult (답변 텍스트 + Slack 블록 + RouteDecision).
    """
    openrouter_call = make_openrouter_call()
    decision = route(text, openrouter_call=openrouter_call)

    if forced_model_id:
        logger.info("수동 모델 선택: %s → %s", decision.model_used, forced_model_id)
        decision.model_used = forced_model_id
        decision.route_reason = f"manual_override:{forced_model_id}"
        decision.fallback_used = False

    answer = await _execute(text, decision)

    blocks = build_response_blocks(
        answer=answer,
        model_used=decision.model_used,
        category=decision.category,
        score=decision.score,
        fallback_used=decision.fallback_used,
    )
    return BridgeResult(answer=answer, blocks=blocks, decision=decision)


async def _execute(text: str, decision: RouteDecision) -> str:
    """결정된 모델로 실제 추론을 실행."""
    if decision.model_used == CLAUDE_A2A.id:
        return await _run_a2a(text, decision)
    return await _run_openrouter(text, decision)


async def _run_a2a(text: str, decision: RouteDecision) -> str:
    """claude.ai A2A 실행. 실패 시 GPT-5.4로 fallback."""
    try:
        from skills.claude_a2a.browser import run_a2a
        answer = await run_a2a(text)
        logger.info("A2A 성공")
        return answer
    except Exception as e:
        logger.error("A2A 실패 (%s), GPT-5.4 fallback", e)
        decision.model_used = GPT_5_4.id
        decision.route_reason = f"a2a_failed_gpt_fallback:{e}"
        decision.fallback_used = True
        decision.a2a_used = False
        return await acall(GPT_5_4.id, text)


async def _run_openrouter(text: str, decision: RouteDecision) -> str:
    """OpenRouter API 호출. 실패 시 RouterError raise."""
    try:
        return await acall(decision.model_used, text)
    except Exception as e:
        raise RouterError(
            f"모델 {decision.model_used} 호출 실패: {e}"
        ) from e

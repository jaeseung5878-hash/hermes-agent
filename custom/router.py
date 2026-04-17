"""
Hermes Smart Router
- Deterministic 라우팅 엔진
- LLM이 최종 모델 선택에 개입하지 않음
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from .classifier import Category, ClassifyResult, classify
from .models import (
    Model,
    GPT_5_4_NANO, GPT_5_4_MINI, GPT_5_4,
    GEMINI_3_1_FLASH_LITE, GEMINI_3_FLASH, GEMINI_3_1_PRO,
    DEEPSEEK_V3_2, DEEPSEEK_V3_2_SPECIALE,
    LLAMA_3_3_70B_FREE, QWEN_3_CODER_FREE, DEEPSEEK_R1_FREE,
    GEMMA_4_26B_FREE, CLAUDE_A2A,
)
from .state import get_state, RouterState
from .errors import RouterError

logger = logging.getLogger(__name__)


@dataclass
class RouteDecision:
    model_used: str
    category: str
    score: int | None
    route_reason: str
    fallback_used: bool
    credit_state: str
    a2a_used: bool
    timestamp: str
    latency_ms: int = 0


def _free_fallback(cat: Category) -> Model:
    if cat == Category.CODE:
        return QWEN_3_CODER_FREE
    if cat == Category.MATH:
        return DEEPSEEK_R1_FREE
    return LLAMA_3_3_70B_FREE


def _route_gemini(score: int, tokens: int) -> Model:
    if tokens > 50_000:
        return GEMINI_3_1_PRO
    return GEMINI_3_FLASH if score >= 3 else GEMINI_3_1_FLASH_LITE


def _route_deepseek(cat: Category) -> Model:
    return DEEPSEEK_V3_2_SPECIALE if cat == Category.MATH else DEEPSEEK_V3_2


def _route_gpt(score: int) -> Model:
    if score >= 4:
        return GPT_5_4
    if score >= 2:
        return GPT_5_4_MINI
    return GPT_5_4_NANO


def _route_normal(cls: ClassifyResult, state: RouterState) -> tuple[Model, str, bool]:
    cat = cls.category

    if cat == Category.IMPORTANT:
        if state.a2a_available():
            return CLAUDE_A2A, "explicit_opus_prefix", True
        return GPT_5_4, "explicit_opus_but_a2a_exhausted", False

    if cat == Category.LONG_DOC:
        if cls.score >= 3 and state.a2a_available():
            return CLAUDE_A2A, "long_doc_complex_reasoning", True
        return GEMINI_3_1_PRO, "long_doc_simple", False

    if cat == Category.CODE:
        if cls.score >= 4 and state.a2a_available():
            return CLAUDE_A2A, "complex_multifile_coding", True
        return _route_deepseek(cat), "deepseek_coding", False

    if cat == Category.MATH:
        return _route_deepseek(cat), "deepseek_reasoning", False

    if cat == Category.SIMPLE:
        return _route_gemini(cls.score, cls.token_estimate), "gemini_simple", False

    if cat in (Category.WEB_SEARCH, Category.TERMINAL):
        return GPT_5_4, "gpt54_web_or_terminal", False

    if cat == Category.DOCUMENT:
        if state.a2a_available():
            return CLAUDE_A2A, "document_writing", True
        return GPT_5_4, "document_but_a2a_exhausted", False

    return _route_gpt(cls.score), "gpt_general_by_score", False


def _route_no_credit(cls: ClassifyResult, state: RouterState) -> tuple[Model, str, bool]:
    if state.a2a_available() and cls.category in (
        Category.DOCUMENT, Category.IMPORTANT, Category.LONG_DOC
    ):
        return CLAUDE_A2A, "no_credit_a2a_priority", True
    return _free_fallback(cls.category), "no_credit_free_fallback", False


def route(text: str, openrouter_call=None) -> RouteDecision:
    started = datetime.now(timezone.utc)
    state = get_state()
    cls = classify(text, openrouter_call=openrouter_call)

    if state.credit_ok():
        model, reason, a2a_used = _route_normal(cls, state)
        credit_state = "normal"
    else:
        model, reason, a2a_used = _route_no_credit(cls, state)
        credit_state = "low"

    fallback_used = credit_state == "low" or "exhausted" in reason

    if a2a_used:
        state.record_a2a_use()

    elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    decision = RouteDecision(
        model_used=model.id,
        category=cls.category.value,
        score=cls.score,
        route_reason=reason,
        fallback_used=fallback_used,
        credit_state=credit_state,
        a2a_used=a2a_used,
        timestamp=started.isoformat(),
        latency_ms=elapsed,
    )
    logger.info(f"Route → {decision}")
    return decision

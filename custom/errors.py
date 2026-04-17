"""Hermes custom 패키지 커스텀 예외."""
from __future__ import annotations


class RouterError(Exception):
    """라우팅 결정 또는 모델 실행 실패."""


class ClassifierError(Exception):
    """분류기 실패 (regex + LLM 모두 실패)."""

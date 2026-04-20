"""
Smart Router → Hermes gateway adapter.

Provides a drop-in replacement for ``agent.smart_model_routing.resolve_turn_route``
so the Hermes gateway pipeline picks models chosen by our deterministic router.

The shim is installed by ``custom.entry`` BEFORE any module imports
``agent.smart_model_routing`` — otherwise bound references (`from X import
resolve_turn_route`) will still hold the original symbol.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .router import route as _smart_route, RouteDecision
from .models import CLAUDE_A2A, GPT_5_4
from .errors import RouterError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_API_MODE = "chat_completions"


def _coerce_primary_runtime(primary: Dict[str, Any]) -> Dict[str, Any]:
    """Return a safe copy of the primary runtime dict (used as last-resort fallback)."""
    return {
        "api_key": primary.get("api_key"),
        "base_url": primary.get("base_url"),
        "provider": primary.get("provider"),
        "api_mode": primary.get("api_mode"),
        "command": primary.get("command"),
        "args": list(primary.get("args") or []),
        "credential_pool": primary.get("credential_pool"),
    }


def _primary_shape(primary: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "model": primary.get("model"),
        "runtime": _coerce_primary_runtime(primary),
        "label": None,
        "signature": (
            primary.get("model"),
            primary.get("provider"),
            primary.get("base_url"),
            primary.get("api_mode"),
            primary.get("command"),
            tuple(primary.get("args") or ()),
        ),
    }


def _substitute_a2a_model(decision: RouteDecision) -> str:
    """Translate A2A pseudo-model to a Hermes-callable OpenRouter model.

    Claude A2A is a Playwright-driven pseudo-model; Hermes's gateway calls LLMs
    via HTTP, so we cannot hand it ``claude-a2a`` as a model ID. Until A2A is
    wired into the Hermes pipeline (Phase 5+), substitute GPT-5.4 and flag the
    decision so logs reflect the fallback.
    """
    logger.warning(
        "A2A route selected but A2A dispatch not yet integrated into Hermes; "
        "substituting %s → %s (reason=%s)",
        decision.model_used,
        GPT_5_4.id,
        decision.route_reason,
    )
    decision.model_used = GPT_5_4.id
    decision.route_reason = f"a2a_not_yet_integrated_gpt_fallback:{decision.route_reason}"
    decision.fallback_used = True
    decision.a2a_used = False
    return GPT_5_4.id


def _openrouter_runtime() -> Dict[str, Any]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # We still return a runtime dict; Hermes will surface the missing-key error
        # when it tries to call the model. FAIL-FAST over silent fallback.
        logger.error("OPENROUTER_API_KEY not set — Hermes will fail on model call")
    return {
        "api_key": api_key,
        "base_url": OPENROUTER_BASE_URL,
        "provider": OPENROUTER_PROVIDER,
        "api_mode": OPENROUTER_API_MODE,
        "command": None,
        "args": [],
        "credential_pool": None,
    }


def resolve_turn_route(
    user_message: str,
    routing_config: Optional[Dict[str, Any]],
    primary: Dict[str, Any],
) -> Dict[str, Any]:
    """Drop-in replacement for ``agent.smart_model_routing.resolve_turn_route``.

    - ``routing_config`` is ignored (our router is self-configured).
    - ``primary`` is used only as a last-resort fallback if our router errors.
    - Returns the dict shape Hermes gateway expects: ``model``, ``runtime``,
      ``label``, ``signature``.
    """
    text = (user_message or "").strip()
    if not text:
        return _primary_shape(primary)

    # Diagnostic: log exactly what the classifier receives so we can catch
    # cases where Hermes wraps the message with history/system prompts.
    _preview = text if len(text) <= 200 else text[:200] + "…"
    logger.info("router input (len=%d): %r", len(text), _preview)

    try:
        decision: RouteDecision = _smart_route(text)
    except RouterError as e:
        logger.error("Smart router failed (%s); falling back to primary", e)
        return _primary_shape(primary)
    except Exception as e:  # defensive
        logger.exception("Unexpected router error (%s); falling back to primary", e)
        return _primary_shape(primary)

    if decision.model_used == CLAUDE_A2A.id:
        _substitute_a2a_model(decision)

    runtime = _openrouter_runtime()

    label = (
        f"smart-router[{decision.category}/score={decision.score}"
        f"/reason={decision.route_reason}] → {decision.model_used}"
    )

    return {
        "model": decision.model_used,
        "runtime": runtime,
        "label": label,
        "signature": (
            decision.model_used,
            runtime["provider"],
            runtime["base_url"],
            runtime["api_mode"],
            runtime["command"],
            tuple(runtime["args"]),
        ),
    }


def install_shim() -> None:
    """Replace ``agent.smart_model_routing.resolve_turn_route`` with ours.

    MUST be called BEFORE ``gateway.run`` (or any module that does
    ``from agent.smart_model_routing import resolve_turn_route``) is imported.
    """
    import agent.smart_model_routing as _sm

    original = _sm.resolve_turn_route
    _sm.resolve_turn_route = resolve_turn_route  # type: ignore[assignment]
    logger.info(
        "Smart Router shim installed: agent.smart_model_routing.resolve_turn_route "
        "replaced (original=%s, new=%s)",
        original.__module__ + "." + original.__name__,
        resolve_turn_route.__module__ + "." + resolve_turn_route.__name__,
    )

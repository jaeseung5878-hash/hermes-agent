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
import re
from typing import Any, Dict, Optional

from .router import route as _smart_route, RouteDecision
from .models import CLAUDE_A2A, GPT_5_4
from .errors import RouterError

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_API_MODE = "chat_completions"

# Hermes gateway wraps inbound messages with a "[username] " prefix (and
# inserts \u200b zero-width spaces into the username to prevent accidental
# mentions) before handing the text to the per-turn route resolver. Strip
# that envelope so our classifier regexes (which are anchored at ^) match the
# actual user question.
_USERNAME_PREFIX = re.compile(r"^\s*\[[^\]]+\]\s+")
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def _strip_wrapper(text: str) -> str:
    """Remove Hermes's ``[username] `` prefix and any zero-width padding."""
    cleaned = _USERNAME_PREFIX.sub("", text, count=1)
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    return cleaned.strip()


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


def _a2a_runtime() -> Dict[str, Any]:
    """Route Claude A2A through the local OpenAI-compatible proxy.

    ``custom.a2a_proxy`` exposes /v1/chat/completions on 127.0.0.1:8888 and
    translates OpenAI-format requests into Playwright calls against claude.ai.
    Hermes treats it like any custom OpenAI endpoint — no special-casing needed
    in the agent pipeline.
    """
    from .a2a_proxy import PROXY_BASE_URL
    return {
        # Proxy ignores this but Hermes may require a non-empty key.
        "api_key": "a2a-local-proxy",
        "base_url": PROXY_BASE_URL,
        "provider": "custom",
        "api_mode": OPENROUTER_API_MODE,  # chat_completions
        "command": None,
        "args": [],
        "credential_pool": None,
    }


def _a2a_session_ready() -> bool:
    """Check whether claude.ai cookies are loadable.

    If no session is configured, we fall back to GPT-5.4 rather than let the
    proxy surface a 502. Keeps the UX sane for users who haven't run
    ``scripts/login_a2a.py`` yet.
    """
    try:
        from skills.claude_a2a.session import session_exists
        return bool(session_exists())
    except Exception as e:
        logger.warning("A2A session probe failed (%s); assuming unavailable", e)
        return False


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
    raw = (user_message or "").strip()
    if not raw:
        return _primary_shape(primary)

    text = _strip_wrapper(raw)
    if not text:
        return _primary_shape(primary)

    try:
        decision: RouteDecision = _smart_route(text)
    except RouterError as e:
        logger.error("Smart router failed (%s); falling back to primary", e)
        return _primary_shape(primary)
    except Exception as e:  # defensive
        logger.exception("Unexpected router error (%s); falling back to primary", e)
        return _primary_shape(primary)

    if decision.model_used == CLAUDE_A2A.id:
        if _a2a_session_ready():
            runtime = _a2a_runtime()
            label = (
                f"smart-router[{decision.category}/claude-a2a/"
                f"reason={decision.route_reason}] → browser automation"
            )
        else:
            # No cookies configured — gracefully degrade to GPT-5.4 instead of
            # handing Hermes a proxy that will 502 on every request.
            logger.warning(
                "CLAUDE_A2A routed but no A2A session found; falling back to %s. "
                "Run scripts/login_a2a.py and set CLAUDE_A2A_COOKIES to enable.",
                GPT_5_4.id,
            )
            decision.model_used = GPT_5_4.id
            decision.route_reason = f"a2a_no_session_gpt_fallback:{decision.route_reason}"
            decision.fallback_used = True
            decision.a2a_used = False
            runtime = _openrouter_runtime()
            label = (
                f"smart-router[{decision.category}/a2a→gpt-fallback"
                f"/score={decision.score}] → {decision.model_used}"
            )
    else:
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

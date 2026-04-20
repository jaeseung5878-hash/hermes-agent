"""
Local OpenAI-compatible proxy for Claude A2A (Playwright-driven claude.ai).

Hermes's gateway talks to LLMs via HTTP (OpenAI ``chat/completions`` shape).
To route the pseudo-model ``claude-a2a`` through a browser automation session
we expose a tiny aiohttp server on 127.0.0.1:8888 that speaks that contract.

Routing flow:
    custom.router selects CLAUDE_A2A
      → custom.router_adapter hands Hermes a runtime dict pointing at
        http://127.0.0.1:8888/v1 with provider=custom, api_mode=chat_completions
      → Hermes posts /chat/completions here as if it were OpenRouter
      → this proxy drives Playwright (skills.claude_a2a.browser.run_a2a)
      → we pack the browser output into OpenAI's response schema

The proxy serializes requests with a semaphore — claude.ai's web UI is a
single-user session; concurrent Playwright drivers would race on the DOM.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from aiohttp import web

logger = logging.getLogger(__name__)

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8888
PROXY_BASE_URL = f"http://{PROXY_HOST}:{PROXY_PORT}/v1"

# Serialize A2A invocations — the claude.ai DOM is shared state per session.
_SEMAPHORE = asyncio.Semaphore(1)


def _stringify_content(content: Any) -> str:
    if isinstance(content, list):
        return " ".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ).strip()
    return str(content or "").strip()


def _extract_prompt(messages: List[Dict[str, Any]]) -> str:
    """Build a compact prompt for claude.ai from Hermes's OpenAI message array.

    claude.ai's web composer is a browser textbox — a full Hermes payload
    (system prompt + tool schemas + history) is too long to paste reliably.
    Strategy:
      1. Take the last user turn (the actual question) verbatim.
      2. Prepend the 2-3 most recent assistant turns for short-term context.
      3. Prepend a brief system hint only if present and short (<400 chars).

    The browser layer (``skills.claude_a2a.browser``) will additionally
    truncate anything above ``A2A_PROMPT_MAX_CHARS`` as a hard cap.
    """
    if not messages:
        return ""

    user_turns: List[str] = []
    assistant_turns: List[str] = []
    system_hint: str = ""

    for msg in messages:
        role = msg.get("role", "user")
        text = _stringify_content(msg.get("content"))
        if not text:
            continue
        if role == "user":
            user_turns.append(text)
        elif role == "assistant":
            assistant_turns.append(text)
        elif role == "system" and not system_hint and len(text) < 400:
            # Only keep very short system prompts; Hermes's tool-use boilerplate
            # is not useful to claude.ai's web UI.
            system_hint = text

    if not user_turns:
        return ""

    last_user = user_turns[-1]
    recent_assistants = assistant_turns[-2:]

    parts: List[str] = []
    if system_hint:
        parts.append(system_hint)
    for a in recent_assistants:
        parts.append(f"[이전 답변]\n{a}")
    parts.append(last_user)
    return "\n\n".join(parts)


def _openai_error(message: str, *, status: int, err_type: str) -> web.Response:
    return web.json_response(
        {"error": {"message": message, "type": err_type}},
        status=status,
    )


async def _dispatch_a2a(prompt: str) -> str:
    """Invoke the Playwright A2A runner with clear error typing."""
    from skills.claude_a2a.browser import run_a2a
    return await run_a2a(prompt)


async def _handle_chat_completions(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception as e:
        return _openai_error(f"invalid json: {e}", status=400, err_type="invalid_request_error")

    messages = payload.get("messages") or []
    prompt = _extract_prompt(messages)
    if not prompt:
        return _openai_error("no messages provided", status=400, err_type="invalid_request_error")

    stream = bool(payload.get("stream"))
    model = payload.get("model") or "claude-a2a"

    async with _SEMAPHORE:
        logger.info("A2A dispatch: prompt=%d chars, stream=%s, model=%s", len(prompt), stream, model)
        try:
            from skills.claude_a2a.session import A2AError
        except Exception:  # pragma: no cover
            A2AError = Exception  # type: ignore[misc]

        try:
            answer = await _dispatch_a2a(prompt)
        except A2AError as e:  # type: ignore[misc]
            logger.error("A2A session failed: %s", e)
            return _openai_error(str(e), status=502, err_type="a2a_session_error")
        except Exception as e:
            logger.exception("A2A dispatch crashed")
            return _openai_error(str(e), status=500, err_type="a2a_internal_error")

    completion_id = f"chatcmpl-a2a-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    prompt_tokens = max(1, len(prompt) // 4)
    completion_tokens = max(1, len(answer) // 4)

    if not stream:
        return web.json_response(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }
        )

    # Streaming response: emit the full answer in a single chunk, then [DONE].
    # Playwright already waited for the full reply so real token-level streaming
    # is not possible — but Hermes still needs the chunked SSE envelope.
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    async def _emit(obj: Dict[str, Any]) -> None:
        await response.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8"))

    await _emit(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": answer}, "finish_reason": None}
            ],
        }
    )
    await _emit(
        {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def _handle_models(_: web.Request) -> web.Response:
    """Stub /v1/models so Hermes's provider probes don't 404."""
    return web.json_response(
        {
            "object": "list",
            "data": [
                {
                    "id": "claude-a2a",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "hermes-a2a-proxy",
                }
            ],
        }
    )


async def _handle_health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_proxy() -> Optional[web.AppRunner]:
    """Start the A2A proxy. Returns the runner so callers can shut it down."""
    app = web.Application()
    app.router.add_post("/v1/chat/completions", _handle_chat_completions)
    app.router.add_get("/v1/models", _handle_models)
    app.router.add_get("/healthz", _handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, PROXY_HOST, PROXY_PORT)
    try:
        await site.start()
    except OSError as e:
        logger.error("A2A proxy failed to bind %s:%d — %s", PROXY_HOST, PROXY_PORT, e)
        await runner.cleanup()
        return None

    logger.info("A2A proxy listening on %s", PROXY_BASE_URL)
    return runner

"""
Slack adapter monkey-patches — fix synthetic thread_id leak.

Upstream Hermes sets ``metadata.thread_id = current_message_ts`` for
**top-level channel messages** as a session-keying device (see
``gateway/platforms/slack.py`` around line 1022: ``thread_ts = event.get(
"thread_ts") or ts``). This breaks the ``platforms.slack.extra.reply_in_thread
= false`` contract on two axes:

1. OUT-BOUND (reply routing): ``_resolve_thread_ts`` sees a truthy
   ``metadata.thread_id`` and posts into a 1-message thread instead of the
   channel. Fixed by ``_patched_resolve_thread_ts`` below.

2. IN-BOUND (session keying): ``build_session_key`` includes thread_id in
   the key, so every top-level mention gets a fresh session — the bot
   remembers nothing between top-level channel messages, only continues
   context inside real threads. Fixed by ``_patched_handle_message``
   which drops the synthetic thread_id on ``event.source`` before the
   base adapter computes the session key.

Signal that a thread_id is *synthetic* (not a real thread root): the stored
thread_id equals the message's own ts. Real threads carry ``thread_id =
thread_parent_ts != current_message_ts``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Populated by install_patch(); holds unbound original to delegate to.
_ORIGINAL_HANDLE_MESSAGE = None


def _patched_resolve_thread_ts(
    self,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Drop-in replacement for ``SlackAdapter._resolve_thread_ts``.

    Behaviour:
      - ``reply_in_thread=True`` (Hermes default) → identical to upstream.
      - ``reply_in_thread=False`` (our default) → return thread_id ONLY when
        it differs from ``reply_to`` (meaning the user is genuinely replying
        inside an existing thread). For synthetic thread_ids (== reply_to)
        and for no-thread cases, return None so the reply lands in the
        channel itself.
    """
    meta = metadata or {}
    metadata_thread_id = meta.get("thread_id") or meta.get("thread_ts")

    # A thread_id that equals the originating message's own ts is a synthetic
    # session key, not a real thread root. Discard it when we're told not to
    # reply in threads.
    is_synthetic = (
        metadata_thread_id is not None
        and reply_to is not None
        and metadata_thread_id == reply_to
    )

    if not self.config.extra.get("reply_in_thread", True):
        if metadata_thread_id and not is_synthetic:
            return metadata_thread_id
        return None

    # reply_in_thread=True — upstream default behaviour.
    if metadata_thread_id:
        return metadata_thread_id
    return reply_to


async def _patched_handle_message(self, event) -> None:
    """Drop synthetic top-level thread_id before session key is computed.

    Top-level @mentions in a Slack channel carry ``source.thread_id ==
    event.message_id`` (both equal the incoming message ts) because upstream
    uses that ts as a session-keying device. Under ``reply_in_thread=False``
    this causes a fresh session per top-level mention — the bot appears to
    "forget" between every message.

    Fix: if the thread_id is synthetic, clear it so ``build_session_key``
    groups messages by ``channel:user`` instead. Real threads (thread_id !=
    message_id) are left untouched — they already share a session across
    replies, which is the desired UX.
    """
    if not self.config.extra.get("reply_in_thread", True):
        source = event.source
        synthetic = (
            event.reply_to_message_id is None
            and source.thread_id is not None
            and source.thread_id == event.message_id
        )
        if synthetic:
            logger.debug(
                "[slack_patch] dropping synthetic thread_id=%s (chat=%s user=%s)",
                source.thread_id, source.chat_id, source.user_id,
            )
            source.thread_id = None

    assert _ORIGINAL_HANDLE_MESSAGE is not None, "install_patch() not called"
    await _ORIGINAL_HANDLE_MESSAGE(self, event)


def install_patch() -> None:
    """Install both Slack adapter monkey-patches.

    1. ``_resolve_thread_ts`` — out-bound reply routing (channel vs thread).
    2. ``handle_message``     — in-bound session keying (drop synthetic
       thread_id so top-level mentions share session with each other).

    Safe to call multiple times; idempotent.
    """
    global _ORIGINAL_HANDLE_MESSAGE

    from gateway.platforms.slack import SlackAdapter

    # --- Patch 1: _resolve_thread_ts ---
    original_resolve = SlackAdapter._resolve_thread_ts
    if not getattr(original_resolve, "__hermes_patched__", False):
        _patched_resolve_thread_ts.__hermes_patched__ = True  # type: ignore[attr-defined]
        SlackAdapter._resolve_thread_ts = _patched_resolve_thread_ts  # type: ignore[assignment]
        logger.info(
            "SlackAdapter._resolve_thread_ts patched to ignore synthetic "
            "thread_ids when reply_in_thread=False"
        )

    # --- Patch 2: handle_message (inherited from BaseAdapter) ---
    current_handle = SlackAdapter.handle_message
    if not getattr(current_handle, "__hermes_patched__", False):
        # Bind as unbound method so our patched wrapper can delegate.
        _ORIGINAL_HANDLE_MESSAGE = current_handle
        _patched_handle_message.__hermes_patched__ = True  # type: ignore[attr-defined]
        SlackAdapter.handle_message = _patched_handle_message  # type: ignore[assignment]
        logger.info(
            "SlackAdapter.handle_message patched to drop synthetic thread_id "
            "on top-level @mentions so channel-level memory persists"
        )

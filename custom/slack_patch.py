"""
Slack adapter monkey-patch — fix synthetic thread_id leak.

Upstream Hermes sets ``metadata.thread_id = current_message_ts`` for
**top-level channel messages** as a session-keying device (see
``gateway/platforms/slack.py`` around line 1022: ``thread_ts = event.get(
"thread_ts") or ts``). This breaks the ``platforms.slack.extra.reply_in_thread
= false`` contract, because ``_resolve_thread_ts`` then sees a truthy
``metadata.thread_id`` and treats every message as already being in a thread.

Signal that a thread_id is *synthetic* (not a real thread root): the stored
thread_id equals ``reply_to`` (which is the originating message's own ts).
Real threads carry ``thread_id = thread_parent_ts != current_message_ts``.

This patch rewrites ``SlackAdapter._resolve_thread_ts`` to honour that
distinction so ``reply_in_thread=False`` actually posts directly into the
channel for top-level mentions, while preserving in-thread replies when the
user is continuing an existing thread conversation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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
    reply_in_thread_enabled = self.config.extra.get("reply_in_thread", True)

    # A thread_id that equals the originating message's own ts is a synthetic
    # session key, not a real thread root. Discard it when we're told not to
    # reply in threads.
    is_synthetic = (
        metadata_thread_id is not None
        and reply_to is not None
        and metadata_thread_id == reply_to
    )

    logger.info(
        "_resolve_thread_ts called: reply_in_thread=%s reply_to=%r "
        "meta_thread_id=%r is_synthetic=%s metadata_keys=%s",
        reply_in_thread_enabled,
        reply_to,
        metadata_thread_id,
        is_synthetic,
        list(meta.keys()),
    )

    if not reply_in_thread_enabled:
        if metadata_thread_id and not is_synthetic:
            logger.info("_resolve_thread_ts → REAL thread %r", metadata_thread_id)
            return metadata_thread_id
        logger.info("_resolve_thread_ts → channel reply (None)")
        return None

    # reply_in_thread=True — upstream default behaviour.
    if metadata_thread_id:
        return metadata_thread_id
    return reply_to


def install_patch() -> None:
    """Swap ``SlackAdapter._resolve_thread_ts`` for our fixed version.

    Safe to call multiple times; idempotent.
    """
    from gateway.platforms.slack import SlackAdapter

    original = SlackAdapter._resolve_thread_ts
    if getattr(original, "__hermes_patched__", False):
        return

    _patched_resolve_thread_ts.__hermes_patched__ = True  # type: ignore[attr-defined]
    SlackAdapter._resolve_thread_ts = _patched_resolve_thread_ts  # type: ignore[assignment]
    logger.info(
        "SlackAdapter._resolve_thread_ts patched to ignore synthetic thread_ids "
        "when reply_in_thread=False"
    )

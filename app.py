"""
Hermes Agent — FastAPI + Slack Bolt 앱 진입점
- POST /slack/events  : Slack 이벤트 (메시지)
- POST /slack/actions : Slack 인터랙티브 액션 (버튼 클릭)
- GET  /health        : Railway 헬스 체크
"""
from __future__ import annotations
import logging
import os
import re

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

load_dotenv()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# ── Slack Bolt 앱 ─────────────────────────────────────────────────────────────
bolt = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN", ""),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
)
handler = AsyncSlackRequestHandler(bolt)

# ── FastAPI 앱 ────────────────────────────────────────────────────────────────
app = FastAPI(title="Hermes Smart Router")

# Bolt가 내부적으로 bot_id를 가져오므로 앱 멘션만 처리
_BOT_MENTION = re.compile(r"<@[A-Z0-9]+>\s*")


def _strip_mention(text: str) -> str:
    return _BOT_MENTION.sub("", text).strip()


# ── 이벤트 핸들러 ─────────────────────────────────────────────────────────────
@bolt.event("app_mention")
async def on_mention(event: dict, say) -> None:
    """봇 멘션 메시지를 처리하고 응답을 전송."""
    text = _strip_mention(event.get("text", ""))
    if not text:
        await say("무엇을 도와드릴까요?")
        return

    from custom.hermes_bridge import handle_message
    from custom.slack_ui import build_alert_blocks

    try:
        result = await handle_message(text)
        await say(blocks=result.blocks, text=result.answer)
    except Exception as e:
        logger.error("메시지 처리 실패: %s", e)
        await say(
            blocks=build_alert_blocks(str(e), level="error"),
            text=f"오류: {e}",
        )


@bolt.event("message")
async def on_dm(event: dict, say, context) -> None:
    """DM 채널 메시지 처리 (멘션 불필요)."""
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return

    text = event.get("text", "").strip()
    if not text:
        return

    from custom.hermes_bridge import handle_message
    from custom.slack_ui import build_alert_blocks

    try:
        result = await handle_message(text)
        await say(blocks=result.blocks, text=result.answer)
    except Exception as e:
        logger.error("DM 처리 실패: %s", e)
        await say(
            blocks=build_alert_blocks(str(e), level="error"),
            text=f"오류: {e}",
        )


# ── 인터랙티브 액션 핸들러 ────────────────────────────────────────────────────
@bolt.action(re.compile(r"^switch_model::"))
async def on_model_switch(ack, action: dict, body: dict, client) -> None:
    """모델 전환 버튼 클릭 처리.

    action_id 형식: switch_model::{model_id}
    """
    await ack()

    model_id: str = action["value"]
    original_text: str = ""

    # 원본 메시지에서 사용자 질문 복원 (첫 번째 section 블록)
    try:
        blocks = body["message"]["blocks"]
        for block in blocks:
            if block.get("type") == "section":
                original_text = block["text"]["text"]
                break
    except (KeyError, IndexError):
        pass

    if not original_text:
        await client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text="원본 메시지를 찾을 수 없습니다. 다시 질문해주세요.",
        )
        return

    from custom.hermes_bridge import handle_message
    from custom.slack_ui import build_alert_blocks

    try:
        result = await handle_message(original_text, forced_model_id=model_id)
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            blocks=result.blocks,
            text=result.answer,
        )
    except Exception as e:
        logger.error("모델 전환 실패: %s", e)
        await client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            blocks=build_alert_blocks(str(e), level="error"),
            text=f"오류: {e}",
        )


# ── FastAPI 라우트 ─────────────────────────────────────────────────────────────
@app.post("/slack/events")
async def slack_events(req: Request) -> Response:
    return await handler.handle(req)


@app.post("/slack/actions")
async def slack_actions(req: Request) -> Response:
    return await handler.handle(req)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}

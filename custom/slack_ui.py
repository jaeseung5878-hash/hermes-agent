"""
Slack Block Kit 기반 모델 선택 UI
- Hermes 답변 + 현재 사용 모델 표시
- 버튼으로 수동 모델 전환 가능
"""
from __future__ import annotations
from .models import (
    GPT_5_4, GPT_5_4_MINI, GPT_5_4_NANO,
    GEMINI_3_1_FLASH_LITE, GEMINI_3_FLASH, GEMINI_3_1_PRO,
    DEEPSEEK_V3_2, DEEPSEEK_V3_2_SPECIALE,
    CLAUDE_A2A, LLAMA_3_3_70B_FREE,
)

MODEL_BUTTONS = [
    ("🧠 Claude Opus (A2A)", CLAUDE_A2A.id),
    ("💠 GPT-5.4",           GPT_5_4.id),
    ("💎 Gemini 3 Flash",    GEMINI_3_FLASH.id),
    ("⚡ DeepSeek V3.2",     DEEPSEEK_V3_2.id),
    ("📄 Gemini 3.1 Pro",    GEMINI_3_1_PRO.id),
    ("🆓 Llama Free",        LLAMA_3_3_70B_FREE.id),
]


def build_response_blocks(
    answer: str,
    model_used: str,
    category: str,
    score: int | None,
    fallback_used: bool,
) -> list[dict]:
    badge     = "⚠️ Fallback" if fallback_used else "✅"
    score_txt = f" · score={score}" if score is not None else ""
    context   = f"{badge} `{model_used}` · category={category}{score_txt}"

    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": label},
            "action_id": f"switch_model::{model_id}",
            "value": model_id,
        }
        for label, model_id in MODEL_BUTTONS
    ]

    row1 = {"type": "actions", "elements": buttons[:3]}
    row2 = {"type": "actions", "elements": buttons[3:]}

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": answer}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        {"type": "divider"},
        row1,
        row2,
    ]


def build_alert_blocks(message: str, level: str = "warning") -> list[dict]:
    emoji = "🚨" if level == "error" else "⚠️"
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} *Alert*\n{message}"},
        }
    ]

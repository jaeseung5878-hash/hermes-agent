"""
Model Registry - OpenRouter 검증된 모델 ID (2026-04-17 기준)
이 파일 외부에서 모델 ID 하드코딩 금지
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class ModelTier(str, Enum):
    T0_CHEAP  = "T0"
    T1_MID    = "T1"
    T2_PREMIUM = "T2"
    FREE      = "FREE"


@dataclass(frozen=True)
class Model:
    id: str
    tier: ModelTier
    input_price: float
    output_price: float
    context_window: int
    strengths: tuple[str, ...]


# OpenAI GPT-5.4
GPT_5_4_NANO = Model("openai/gpt-5.4-nano", ModelTier.T1_MID, 0.20, 1.25, 400_000, ("classifier", "short_tasks", "low_latency"))
GPT_5_4_MINI = Model("openai/gpt-5.4-mini", ModelTier.T1_MID, 0.75, 4.50, 400_000, ("general", "balanced"))
GPT_5_4      = Model("openai/gpt-5.4", ModelTier.T2_PREMIUM, 2.50, 15.00, 1_050_000, ("web_search", "terminal", "computer_use", "general_flagship"))

# Google Gemini 3.x
GEMINI_3_1_FLASH_LITE = Model("google/gemini-3.1-flash-lite-preview", ModelTier.T0_CHEAP, 0.25, 1.50, 1_048_576, ("simple", "translation", "fast"))
GEMINI_3_FLASH        = Model("google/gemini-3-flash-preview", ModelTier.T0_CHEAP, 0.50, 3.00, 1_048_576, ("agentic", "coding_assist"))
GEMINI_3_1_PRO        = Model("google/gemini-3.1-pro-preview", ModelTier.T0_CHEAP, 2.00, 12.00, 1_048_576, ("long_context", "multimodal"))

# DeepSeek V3.2
DEEPSEEK_V3_2          = Model("deepseek/deepseek-v3.2", ModelTier.T0_CHEAP, 0.26, 0.38, 163_840, ("coding", "efficient"))
DEEPSEEK_V3_2_SPECIALE = Model("deepseek/deepseek-v3.2-speciale", ModelTier.T0_CHEAP, 0.26, 0.38, 163_840, ("reasoning", "math", "algorithm"))

# 무료 모델
LLAMA_3_3_70B_FREE    = Model("meta-llama/llama-3.3-70b-instruct:free", ModelTier.FREE, 0.0, 0.0, 66_000, ("general_fallback",))
QWEN_3_CODER_FREE     = Model("qwen/qwen3-coder:free", ModelTier.FREE, 0.0, 0.0, 262_000, ("coding_fallback",))
DEEPSEEK_R1_FREE      = Model("deepseek/deepseek-r1-0528:free", ModelTier.FREE, 0.0, 0.0, 163_840, ("reasoning_fallback",))
GEMMA_4_26B_FREE      = Model("google/gemma-4-26b-a4b-it:free", ModelTier.FREE, 0.0, 0.0, 262_000, ("lightweight_last_resort",))
OPENROUTER_AUTO_FREE  = Model("openrouter/auto", ModelTier.FREE, 0.0, 0.0, 100_000, ("auto_router_free",))

# Claude A2A (API 아님, 브라우저 자동화)
CLAUDE_A2A = Model("claude-a2a", ModelTier.T2_PREMIUM, 0.0, 0.0, 1_000_000, ("documents", "research", "writing", "complex_code", "deep_reasoning"))

ALL_MODELS: tuple[Model, ...] = (
    GPT_5_4_NANO, GPT_5_4_MINI, GPT_5_4,
    GEMINI_3_1_FLASH_LITE, GEMINI_3_FLASH, GEMINI_3_1_PRO,
    DEEPSEEK_V3_2, DEEPSEEK_V3_2_SPECIALE,
    LLAMA_3_3_70B_FREE, QWEN_3_CODER_FREE, DEEPSEEK_R1_FREE,
    GEMMA_4_26B_FREE, OPENROUTER_AUTO_FREE,
    CLAUDE_A2A,
)

def get_model(model_id: str) -> Model | None:
    return next((m for m in ALL_MODELS if m.id == model_id), None)

"""
Intent Classifier
1차: regex 기반 deterministic
2차: gpt-5.4-nano (regex 실패 시만)
"""
from __future__ import annotations
import re
import logging
from enum import Enum
from dataclasses import dataclass
from .models import GPT_5_4_NANO

logger = logging.getLogger(__name__)


class Category(str, Enum):
    SIMPLE    = "SIMPLE"
    CODE      = "CODE"
    MATH      = "MATH"
    LONG_DOC  = "LONG_DOC"
    WEB_SEARCH = "WEB_SEARCH"
    TERMINAL  = "TERMINAL"
    DOCUMENT  = "DOCUMENT"
    IMPORTANT = "IMPORTANT"
    GENERAL   = "GENERAL"


@dataclass
class ClassifyResult:
    category: Category
    score: int
    token_estimate: int
    explicit_opus: bool
    used_llm: bool


PATTERNS = {
    Category.CODE: re.compile(
        r"(코드|짜줘|구현|함수|클래스|디버그|버그|리팩터|\.py|\.js|\.ts|\.go|\.rs|코딩|프로그래밍|스크립트)",
        re.IGNORECASE,
    ),
    Category.MATH: re.compile(
        r"(계산|풀어|증명|수학|algorithm|알고리즘|최적화|방정식|적분|미분|확률|통계)",
        re.IGNORECASE,
    ),
    Category.WEB_SEARCH: re.compile(
        r"(검색|찾아줘|최신|뉴스|요즘|지금|오늘|이번 주|search)",
        re.IGNORECASE,
    ),
    Category.TERMINAL: re.compile(
        r"(명령어|쉘|터미널|bash|zsh|실행|run|command)",
        re.IGNORECASE,
    ),
    Category.DOCUMENT: re.compile(
        r"(보고서|논문|계약서|문서\s*작성|리포트|제안서|기획서|report)",
        re.IGNORECASE,
    ),
    Category.SIMPLE: re.compile(
        r"^(안녕|hi|hello|뭐야|뭐임|알려줘|번역|요약|무엇|언제|어디)",
        re.IGNORECASE,
    ),
}

OPUS_PREFIX = re.compile(r"^\s*!opus\b", re.IGNORECASE)
EXPLAIN     = re.compile(r"(설명해|왜|어떻게|why|how)", re.IGNORECASE)
COMPARE     = re.compile(r"(차이|비교|vs|versus)", re.IGNORECASE)
MULTI_STEP  = re.compile(r"(그리고|또한|and also|여러)", re.IGNORECASE)
CODE_BLOCK  = re.compile(r"```")


def estimate_tokens(text: str) -> int:
    return int(len(text) * 0.7)


def calculate_score(text: str) -> int:
    score = 0
    if estimate_tokens(text) > 300: score += 1
    if EXPLAIN.search(text):        score += 1
    if CODE_BLOCK.search(text):     score += 1
    if COMPARE.search(text):        score += 1
    if MULTI_STEP.search(text):     score += 1
    return score


def regex_classify(text: str) -> Category | None:
    for cat in [Category.CODE, Category.MATH, Category.DOCUMENT,
                Category.TERMINAL, Category.WEB_SEARCH, Category.SIMPLE]:
        if PATTERNS[cat].search(text):
            return cat
    return None


def llm_classify(text: str, openrouter_call) -> Category:
    prompt = f"""아래 질문을 정확히 하나의 카테고리로 분류해주세요.
카테고리: SIMPLE, CODE, MATH, WEB_SEARCH, TERMINAL, DOCUMENT, GENERAL
반드시 카테고리 이름만 대문자로 한 단어만 출력하세요.
질문: {text[:500]}"""
    try:
        result = openrouter_call(model_id=GPT_5_4_NANO.id, prompt=prompt, max_tokens=10)
        cat_str = result.strip().upper()
        if cat_str in Category.__members__:
            return Category[cat_str]
    except Exception as e:
        logger.warning(f"LLM 분류 실패: {e}")
    return Category.GENERAL


def classify(text: str, openrouter_call=None) -> ClassifyResult:
    explicit_opus = bool(OPUS_PREFIX.match(text))
    clean_text = OPUS_PREFIX.sub("", text).strip() if explicit_opus else text
    tokens = estimate_tokens(clean_text)
    score = calculate_score(clean_text)

    if tokens > 50_000:
        return ClassifyResult(Category.LONG_DOC, score, tokens, explicit_opus, False)
    if explicit_opus:
        return ClassifyResult(Category.IMPORTANT, score, tokens, True, False)

    cat = regex_classify(clean_text)
    if cat is not None:
        return ClassifyResult(cat, score, tokens, False, False)

    if openrouter_call is not None:
        cat = llm_classify(clean_text, openrouter_call)
        return ClassifyResult(cat, score, tokens, False, True)

    return ClassifyResult(Category.GENERAL, score, tokens, False, False)

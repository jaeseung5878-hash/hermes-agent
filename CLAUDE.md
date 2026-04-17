# CLAUDE.md

이 파일은 Claude Code가 이 프로젝트에서 작업할 때 자동으로 참조하는 핵심 규칙 문서입니다.

## 프로젝트 개요
목적: NousResearch Hermes Agent(MIT 오픈소스)를 포크하여 Smart Router와 Slack 버튼 UI를 추가한 뒤 Railway에 배포.

핵심 기능 3가지:
1. Hermes Agent 기존 기능 전부 유지
2. Smart Router 추가 (자동 모델 라우팅, deterministic)
3. Slack 버튼 UI로 모델 수동 변경 가능

## 절대 규칙 (ABSOLUTE RULES)
1. NO LLM-based routing — 라우팅 결정은 서버가 주관. 예외: regex 실패 시 gpt-5.4-nano로 intent 분류는 허용. 단 최종 모델 선택은 서버 코드가 결정.
2. NO runtime self-interpretation — 라우터가 자기 규칙을 LLM에게 물어보면 안 됨
3. NO execution loops — LLM이 라우터를 재호출하는 구조 금지
4. NO ambiguity in model selection — 라우팅 결과는 항상 단 하나의 모델 ID
5. NO Claude automation via API — Claude는 A2A(브라우저 자동화)로만 사용
6. SERVER IS SINGLE SOURCE OF TRUTH — 모든 상태는 서버에서 관리
7. FAIL FAST IS PREFERRED OVER GUESSING — 불확실하면 fallback보다 명시적 에러 반환

## Smart Router 라우팅 규칙

### T0 스코어링
score = 0
+1 if 토큰 > 300
+1 if 설명/왜/어떻게 감지
+1 if 코드 블록
+1 if 비교/차이/vs 감지
+1 if 멀티스텝/그리고/또한

### 1차 분류기 (regex)
- SIMPLE: 안녕, 뭐야, 번역, 요약
- CODE: 코드, 짜줘, 함수, .py, .js, 구현, 디버그
- MATH: 계산, 풀어, 증명, 수학, 알고리즘
- LONG_DOC: 토큰 > 50,000
- WEB_SEARCH: 검색, 찾아줘, 최신, 뉴스
- TERMINAL: 명령어, 쉘, 터미널, bash
- IMPORTANT: !opus 프리픽스
- DOCUMENT: 보고서, 논문, 계약서, 문서 작성

### 작업별 모델 배정
[1순위] 저렴/빠름
- SIMPLE score<=2 → google/gemini-3.1-flash-lite-preview
- SIMPLE score>=3 → google/gemini-3-flash-preview
- SIMPLE 토큰50k+ → google/gemini-3.1-pro-preview
- CODE 일반 → deepseek/deepseek-v3.2
- MATH/추론 → deepseek/deepseek-v3.2-speciale

[2순위] GPT 3단계
- score<=1 → openai/gpt-5.4-nano
- score 2~3 → openai/gpt-5.4-mini
- score>=4 → openai/gpt-5.4

[3순위] Claude A2A
- 복잡한 문서/연구/분석 → claude-a2a
- 복잡한 코딩 멀티파일 → claude-a2a
- 토큰50k+ 복잡추론 → claude-a2a
- !opus 프리픽스 명시 → claude-a2a 강제

[GPT 전담]
- 웹검색/브라우징 → openai/gpt-5.4
- 터미널/자동화 → openai/gpt-5.4

[Fallback 체인]
- 정상: 기본모델 실패 → GPT-5.4 → Claude A2A → 무료모델
- 크레딧 $5.1 미만: Claude A2A + 무료모델만
- A2A 한계 도달: GPT-5.4

## 검증된 모델 ID (2026-04-17 기준)
- openai/gpt-5.4-nano
- openai/gpt-5.4-mini
- openai/gpt-5.4
- google/gemini-3.1-flash-lite-preview
- google/gemini-3-flash-preview
- google/gemini-3.1-pro-preview
- deepseek/deepseek-v3.2
- deepseek/deepseek-v3.2-speciale
- meta-llama/llama-3.3-70b-instruct:free
- qwen/qwen3-coder:free
- deepseek/deepseek-r1-0528:free
- google/gemma-4-26b-a4b-it:free
- openrouter/auto

## Claude A2A 연동
- Playwright로 claude.ai 웹 접속
- 로그인 세션 쿠키 저장
- 질문 입력 → 응답 대기 → 답변 파싱
- 일일 50회 / 주간 250회 한계 추적
- API 호출 아님 → 토큰 과금 없음

## 크레딧 관리
- OpenRouter 잔액 30분 간격 확인
- $5.1 미만 시 유료 모델 비활성화 + Slack 알림
- A2A 초과 시 GPT-5.4 fallback + Slack 알림

## Output Contract
모든 라우팅 결과:
{
  "model_used": str,
  "category": str,
  "score": int | None,
  "route_reason": str,
  "fallback_used": bool,
  "credit_state": str,
  "a2a_used": bool,
  "timestamp": str,
  "latency_ms": int
}

## 코딩 스타일
- Python 3.11+
- from __future__ import annotations + 명시적 타입
- Docstring: Google 스타일
- 에러: RouterError, ClassifierError, A2AError
- 로깅: logging 모듈, INFO 레벨
- 모델 ID 하드코딩 금지 → custom/models.py에서만

## 커밋 전 체크리스트
- 모든 모델 ID가 custom/models.py에서 import되는가
- 절대 규칙 7가지 위반 없는가
- 라우팅 결정이 deterministic한가
- Fallback 체인 전부 커버되는가
- 테스트 통과하는가

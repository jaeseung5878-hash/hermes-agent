# Claude A2A Skill

Playwright를 사용해 claude.ai 웹 UI를 자동화하는 스킬입니다.  
OpenRouter API 비용 없이 Claude 수준의 응답을 얻기 위해 브라우저 자동화로 접근합니다.

## 구조

| 파일 | 역할 |
|------|------|
| `session.py` | 로그인 쿠키 저장/로드/주입/추출 |
| `browser.py` | Playwright 조작, 응답 파싱 |
| `__init__.py` | 공개 API 노출 |

## 세션 초기화 (최초 1회 또는 만료 시)

headless=False 모드로 수동 로그인 후 쿠키를 저장합니다.

```python
import asyncio
from playwright.async_api import async_playwright
from skills.claude_a2a.session import extract_session

async def login():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://claude.ai")
        input("브라우저에서 로그인 완료 후 Enter 입력...")
        await extract_session(context)
        await browser.close()

asyncio.run(login())
```

세션 파일 위치: `~/.hermes/claude_a2a_session.json`

## 질의 (세션 초기화 이후)

```python
from skills.claude_a2a.browser import run_a2a_sync

answer = run_a2a_sync("복잡한 문서 분석 또는 연구 요청...")
print(answer)
```

## 한계 및 주의사항

- **일일 50회 / 주간 250회** 사용 한계 (RouterState에서 추적)
- 한계 초과 시 `router.py`가 자동으로 GPT-5.4 fallback 적용
- claude.ai DOM 변경 시 `browser.py`의 selector 업데이트 필요
- API 호출이 아님 → 토큰 과금 없음
- 로그인 쿠키 만료 시 재로그인 후 `extract_session()` 재실행 필요

## Router 통합

`custom/router.py`에서 A2A는 다음 조건에서만 선택됩니다.

- `!opus` 프리픽스 명시 (`Category.IMPORTANT`)
- 복잡한 문서/연구 (`Category.DOCUMENT`, A2A 가용 시)
- 복잡한 멀티파일 코딩 (`Category.CODE`, score ≥ 4)
- 긴 문서 + 복잡 추론 (`Category.LONG_DOC`, score ≥ 3)
- 크레딧 부족 시 일부 카테고리 A2A 우선 사용

## 오류 처리

모든 실패는 `A2AError`를 raise합니다.  
`router.py`는 A2A 실패 시 GPT-5.4로 자동 fallback합니다.

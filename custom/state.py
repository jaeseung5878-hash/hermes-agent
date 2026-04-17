"""
Hermes Smart Router 상태 관리
- OpenRouter 크레딧 잔액 추적
- Claude A2A 사용량 카운터
"""
from __future__ import annotations
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import requests

logger = logging.getLogger(__name__)

CREDIT_THRESHOLD = 5.1
CREDIT_CHECK_INTERVAL = 30 * 60  # 30분
A2A_DAILY_LIMIT = 50
A2A_WEEKLY_LIMIT = 250
STATE_FILE = Path.home() / ".hermes" / "custom_router_state.json"


@dataclass
class RouterState:
    openrouter_credit: float = 0.0
    credit_checked_at: float = 0.0
    a2a_daily_used: int = 0
    a2a_weekly_used: int = 0
    a2a_day: str = ""
    a2a_week: str = ""

    @classmethod
    def load(cls) -> "RouterState":
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                return cls(**data)
            except Exception as e:
                logger.warning(f"상태 파일 로드 실패: {e}, 새로 생성합니다.")
        return cls()

    def save(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(self.__dict__, indent=2))

    def refresh_credit_if_stale(self) -> None:
        if time.time() - self.credit_checked_at < CREDIT_CHECK_INTERVAL:
            return
        self._fetch_credit()

    def _fetch_credit(self) -> None:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.error("OPENROUTER_API_KEY 환경변수 없음")
            return
        try:
            r = requests.get(
                "https://openrouter.ai/api/v1/credits",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()["data"]
            self.openrouter_credit = data["total_credits"] - data["total_usage"]
            self.credit_checked_at = time.time()
            self.save()
        except Exception as e:
            logger.error(f"OpenRouter 크레딧 조회 실패: {e}")

    def credit_ok(self) -> bool:
        self.refresh_credit_if_stale()
        return self.openrouter_credit >= CREDIT_THRESHOLD

    def _reset_if_new_period(self) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        year_week = now.strftime("%G-W%V")
        if today != self.a2a_day:
            self.a2a_daily_used = 0
            self.a2a_day = today
        if year_week != self.a2a_week:
            self.a2a_weekly_used = 0
            self.a2a_week = year_week

    def record_a2a_use(self) -> None:
        self._reset_if_new_period()
        self.a2a_daily_used += 1
        self.a2a_weekly_used += 1
        self.save()

    def a2a_available(self) -> bool:
        self._reset_if_new_period()
        return (self.a2a_daily_used < A2A_DAILY_LIMIT and
                self.a2a_weekly_used < A2A_WEEKLY_LIMIT)


_state: RouterState | None = None

def get_state() -> RouterState:
    global _state
    if _state is None:
        _state = RouterState.load()
    return _state

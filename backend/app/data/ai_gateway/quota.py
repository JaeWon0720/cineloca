"""AI 공급자 일일 호출 수와 한도 (NFR-005·018, SDD 2.4 강등 표).

- 공급자를 부르기 '직전'에 한 번씩 센다(재요청 포함). Gemini 무료 등급은 실패한 요청도 한도에 든다.
- 한도에 닿으면 그 공급자를 건너뛴다(skipped_quota). 로컬이 실패했으면 바로 강등된다.
- 70%를 넘으면 하루 한 번 경고 로그를 남긴다(관리자 리포트는 usage_counter를 읽는다).
- 호출 수 저장소(DB)가 실패하면 막지 않고 부른다(fail-open). 서비스가 DB 장애로 AI까지 멈추지 않게.
하루는 한국 시간 자정에 바뀐다.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))
WARN_RATIO = 0.7


def today_kst() -> date:
    return datetime.now(KST).date()


class UsageCounter(Protocol):
    """usage_counter 테이블. 구현은 store.PgUsageCounter."""

    async def reserve(self, day: date, key: str, limit: int | None) -> int | None:
        """한도 안이면 1 올리고 올린 뒤 값을, 이미 한도면 올리지 않고 None을 돌려준다."""
        ...


class DailyQuota:
    def __init__(
        self,
        counter: UsageCounter,
        limits: Mapping[str, int | None],
        *,
        today: Callable[[], date] = today_kst,
        warn_ratio: float = WARN_RATIO,
    ) -> None:
        self._counter = counter
        self._limits = dict(limits)  # 없는 공급자(ollama 등)는 한도 없이 세기만 한다
        self._today = today
        self._warn_ratio = warn_ratio
        self._warned: set[tuple[date, str, str]] = set()

    async def reserve(self, provider: str) -> bool:
        """이 공급자를 지금 한 번 불러도 되면 True(호출 수 1 증가)."""
        day = self._today()
        limit = self._limits.get(provider)
        try:
            count = await self._counter.reserve(day, provider, limit)
        except Exception:  # DB 장애: 막지 않는다. 접속 정보가 섞일 수 있어 예외 내용은 남기지 않음
            logger.warning("AI 호출 수 기록 실패 provider=%s", provider)
            return True
        if count is None:
            self._warn_once(
                day, provider, "limit", "AI 일일 한도 100%%, 건너뜀 provider=%s limit=%s", limit
            )
            return False
        if limit and count >= math.ceil(limit * self._warn_ratio):
            self._warn_once(
                day, provider, "warn", "AI 일일 한도 70%% 넘음 provider=%s limit=%s", limit
            )
        return True

    def _warn_once(
        self, day: date, provider: str, level: str, message: str, limit: int | None
    ) -> None:
        if (day, provider, level) in self._warned:
            return
        self._warned.add((day, provider, level))
        logger.warning(message, provider, limit)

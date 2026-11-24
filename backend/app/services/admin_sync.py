"""관리자 동기화 요청·리포트 (API-021, FR-612·613·616, NFR-005·018, ADR-012).

API는 배치를 직접 부르지 않는다. 요청은 sync_run에 'requested' 행을 넣는 것까지이고,
배치(python -m app.batch --watch)가 1분 안에 가져가 실행한다. 조회는 최근 10회 리포트(건수 가드
경고 포함), 지금 대기·실행 중인 동기화, 오늘 외부 호출 수(Gemini 한도 70% 경고), 정기 실행 주기다.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Protocol

import asyncpg

from app.batch.report import SyncReport
from app.data import db, sync_runs
from app.data.ai_gateway.quota import today_kst

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]
UsageLevel = Literal["ok", "warning", "exhausted"]

WARN_RATIO = 0.7  # 70%면 관리자 화면 경고, 100%면 그 공급자를 건너뜀(SDD 2.3)
DATASET_DAILY_LIMIT = 1_000  # FR-601: 데이터셋별 하루 호출 상한


class UsageSource(Protocol):
    async def counts(self, day: date) -> dict[str, int]: ...


@dataclass(frozen=True)
class ActiveRun:
    run_id: int
    trigger: str
    status: str
    requested_at: datetime
    started_at: datetime | None


@dataclass(frozen=True)
class UsageItem:
    key: str
    count: int
    limit: int | None
    ratio: float | None
    level: UsageLevel


@dataclass(frozen=True)
class Schedule:
    interval_days: int  # 0이면 끔
    last_started_at: datetime | None
    next_due_at: datetime | None  # 끔이면 None. 지난 시각이면 배치가 다음 확인 때 시작


@dataclass(frozen=True)
class SyncOverview:
    active: ActiveRun | None
    reports: list[SyncReport]
    usage: list[UsageItem]
    usage_day: date
    schedule: Schedule
    alerts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncRequest:
    run_id: int
    created: bool  # False면 이미 대기·실행 중이던 동기화


def _limit_of(key: str, gemini_limit: int) -> int | None:
    if key == "gemini":
        return gemini_limit
    if key.startswith("DATA-"):
        return DATASET_DAILY_LIMIT
    return None


def usage_items(counts: dict[str, int], gemini_limit: int) -> list[UsageItem]:
    items = []
    for key in sorted(counts):
        n, limit = counts[key], _limit_of(key, gemini_limit)
        ratio = round(n / limit, 4) if limit else None
        level: UsageLevel = "ok"
        if ratio is not None and ratio >= 1:
            level = "exhausted"
        elif ratio is not None and ratio >= WARN_RATIO:
            level = "warning"
        items.append(UsageItem(key, n, limit, ratio, level))
    return items


def alerts_for(reports: list[SyncReport], usage: list[UsageItem]) -> list[str]:
    """화면 위쪽에 띄울 경고(FR-612 건수 가드, NFR-018 호출 한도)."""
    out = []
    if reports and reports[0].status == "guarded":
        out.append("최근 동기화가 건수 가드에 걸려 반영되지 않았습니다(직전 데이터 유지).")
    if reports and reports[0].status == "failed":
        out.append("최근 동기화가 실패했습니다. 실패 목록을 확인해 주세요.")
    for u in usage:
        if u.level == "exhausted":
            out.append(f"{u.key} 오늘 호출 한도({u.limit}회)를 모두 썼습니다.")
        elif u.level == "warning":
            out.append(f"{u.key} 오늘 호출이 한도의 {round((u.ratio or 0) * 100)}%입니다.")
    return out


class AdminSyncService:
    def __init__(
        self,
        usage: UsageSource,
        *,
        gemini_limit: int,
        interval_days: int,
        connect: Connect = db.connection,
        today: Callable[[], date] = today_kst,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._usage = usage
        self._gemini_limit = gemini_limit
        self._interval = interval_days
        self._connect = connect
        self._today = today
        self._now = now

    async def request(self) -> SyncRequest:
        async with self._connect() as conn:
            run_id, created = await sync_runs.request_manual(conn)
        return SyncRequest(run_id, created)

    async def overview(self) -> SyncOverview:
        async with self._connect() as conn:
            row = await sync_runs.active_run(conn)
            reports = await sync_runs.recent_reports(conn)
            last = await sync_runs.last_started_at(conn)
        day = self._today()
        usage = usage_items(await self._usage.counts(day), self._gemini_limit)
        active = None
        if row is not None:
            active = ActiveRun(
                row["id"], row["trigger"], row["status"], row["created_at"], row["started_at"]
            )
        next_due = None
        if self._interval > 0:
            next_due = (last + timedelta(days=self._interval)) if last else self._now()
        return SyncOverview(
            active=active,
            reports=reports,
            usage=usage,
            usage_day=day,
            schedule=Schedule(self._interval, last, next_due),
            alerts=alerts_for(reports, usage),
        )

"""sync_run 기록·반영 결정 DB 테스트 (FR-612·613, ADR-012, #31).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다.
sync_run을 비우므로 개발 DB를 쓰면 안 된다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_sync_record_db.py
"""

import asyncio
import os
from collections.abc import Awaitable, Callable

import asyncpg
import pytest

from app.batch.report import GeoCounts, MatchCounts, SyncCounts, failure
from app.batch.sync_record import guard_and_publish, record_failure
from app.data import sync_runs

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("TRUNCATE sync_run RESTART IDENTITY")
            await conn.execute("CREATE TEMP TABLE published (n int)")
            await body(conn)
        finally:
            await conn.close()

    asyncio.run(main())


def counts(works: int, locations: int) -> SyncCounts:
    return SyncCounts(
        fetched={"DATA-01": locations},
        works=works,
        locations=locations,
        match=MatchCounts(matched=works),
        geo=GeoCounts(located=locations),
    )


async def mark_published(conn: asyncpg.Connection) -> None:
    await conn.execute("INSERT INTO published VALUES (1)")


async def published(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT count(*) FROM published")


def test_success_then_guarded_keeps_previous_data() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        first = await sync_runs.start(conn, "manual")
        ok = await guard_and_publish(conn, first, counts(400, 1300), [], mark_published)

        second = await sync_runs.start(conn, "schedule")
        fails = [failure("fetch", "timeout", dataset="DATA-02")]
        bad = await guard_and_publish(conn, second, counts(400, 600), fails, mark_published)

        assert ok.passed and not bad.passed
        assert await published(conn) == 1  # 가드 위반은 반영하지 않음
        latest, older = await sync_runs.recent_reports(conn)
        assert (latest.run_id, latest.status, older.status) == (second, "guarded", "succeeded")
        assert latest.prev_counts == counts(400, 1300)  # 직전 성공과 비교
        assert latest.guard == [
            "촬영지 수가 직전 1300곳에서 600곳으로 53.8% 줄었습니다(30% 이상 감소)"
        ]
        assert [f.dataset for f in latest.failures] == ["DATA-02"]
        assert latest.rates and latest.rates.geo_rate == 1.0
        assert latest.started_at and latest.finished_at

        # 가드에 막힌 결과는 비교 기준이 되지 않는다
        assert await sync_runs.last_succeeded_counts(conn) == counts(400, 1300)

    run(body)


def test_publish_error_rolls_back_and_records_failure() -> None:
    async def broken(conn: asyncpg.Connection) -> None:
        await mark_published(conn)
        raise RuntimeError("교체 실패 https://example.com/x?serviceKey=abc")

    async def body(conn: asyncpg.Connection) -> None:
        run_id = await sync_runs.start(conn, "manual")
        with pytest.raises(RuntimeError):
            await guard_and_publish(conn, run_id, counts(400, 1300), [], broken)

        assert await published(conn) == 0  # 교체도 되돌림
        (report,) = await sync_runs.recent_reports(conn)
        assert report.status == "failed"
        assert report.failures[0].step == "publish"
        assert "abc" not in report.failures[0].reason
        assert await sync_runs.last_succeeded_counts(conn) is None

    run(body)


def test_record_failure_during_collection() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        run_id = await sync_runs.start(conn, "schedule")
        await record_failure(conn, run_id, [failure("fetch", "응답 형식 변경", "DATA-01")])

        (report,) = await sync_runs.recent_reports(conn)
        assert report.status == "failed" and report.counts is None
        assert report.failures[0].reason == "응답 형식 변경"

    run(body)


def test_prune_keeps_ten_and_last_success() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        first = await sync_runs.start(conn, "manual")
        await guard_and_publish(conn, first, counts(400, 1300), [], mark_published)
        for _ in range(12):
            rid = await sync_runs.start(conn, "schedule")
            await record_failure(conn, rid, [failure("fetch", "timeout")])

        ids = [r["id"] for r in await conn.fetch("SELECT id FROM sync_run ORDER BY id")]
        assert len(ids) == 11 and ids[0] == first  # 최근 10회 + 마지막 성공
        assert await sync_runs.last_succeeded_counts(conn) == counts(400, 1300)

    run(body)

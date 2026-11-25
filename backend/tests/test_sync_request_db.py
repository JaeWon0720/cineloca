"""관리자 동기화 요청·배치 감시 DB 테스트 (API-021, FR-616, ADR-012).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다.
sync_run을 비우므로 개발 DB를 쓰면 안 된다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_sync_request_db.py
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.batch.__main__ import next_job
from app.data import sync_runs

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")
NOW = datetime.now(timezone.utc)


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("TRUNCATE sync_run RESTART IDENTITY")
            await body(conn)
        finally:
            await conn.close()

    asyncio.run(main())


def test_manual_request_once_then_claimed_by_batch() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        first = await sync_runs.request_manual(conn)
        again = await sync_runs.request_manual(conn)
        assert first == (1, True) and again == (1, False)  # 대기 중이면 새로 넣지 않음
        assert (await sync_runs.active_run(conn))["status"] == "requested"

        assert await next_job(conn, 7, NOW) == ("manual", 1)
        assert (await sync_runs.active_run(conn))["status"] == "running"
        assert await sync_runs.request_manual(conn) == (1, False)  # 실행 중에도 막음
        assert await sync_runs.claim_requested(conn) is None

    run(body)


def test_concurrent_requests_make_one_row() -> None:
    async def main() -> None:
        conns = [await asyncpg.connect(DSN) for _ in range(5)]
        try:
            await conns[0].execute("TRUNCATE sync_run RESTART IDENTITY")
            results = await asyncio.gather(*(sync_runs.request_manual(c) for c in conns))
            assert sum(created for _, created in results) == 1
            assert {run_id for run_id, _ in results} == {1}
        finally:
            for c in conns:
                await c.close()

    asyncio.run(main())


def test_stale_running_is_closed_and_new_request_allowed() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await conn.execute(
            "INSERT INTO sync_run (trigger, status, started_at) "
            "VALUES ('schedule', 'running', now() - interval '4 hours')"
        )
        assert await sync_runs.active_run(conn) is None  # 3시간 넘게 멈춘 기록은 실행 중이 아님
        assert await sync_runs.fail_stale(conn) == 1
        row = await conn.fetchrow("SELECT status, failures::text AS f FROM sync_run WHERE id = 1")
        assert row["status"] == "failed" and "멈췄습니다" in row["f"]
        assert await sync_runs.request_manual(conn) == (2, True)

    run(body)


def test_schedule_runs_when_interval_passed() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        assert await next_job(conn, 7, NOW) == ("schedule", None)  # 한 번도 안 돌았으면 바로
        assert await next_job(conn, 0, NOW) == ("idle", None)  # 0이면 정기 실행 끔
        await conn.execute(
            "INSERT INTO sync_run (trigger, status, started_at, finished_at) "
            "VALUES ('manual', 'succeeded', $1, $1)",
            NOW - timedelta(days=2),
        )
        assert await next_job(conn, 7, NOW) == ("idle", None)  # 수동 실행도 마지막 실행으로 침
        assert await next_job(conn, 2, NOW) == ("schedule", None)
        assert await sync_runs.last_started_at(conn) == NOW - timedelta(days=2)

    run(body)

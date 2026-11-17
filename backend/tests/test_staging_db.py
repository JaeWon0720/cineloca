"""스테이징 → 서비스 테이블 반영 DB 테스트 (FR-607·611, ADR-012).

스키마(db/init 01~03)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 작품·촬영지를 비우므로
개발 DB를 쓰면 안 된다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_staging_db.py
"""

import asyncio
import os
from collections.abc import Awaitable, Callable

import asyncpg
import pytest

from app.data import staging

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "TRUNCATE review_item, shooting, work, location, station RESTART IDENTITY CASCADE"
            )
            await body(conn)
        finally:
            await conn.close()

    asyncio.run(main())


def work(key: str, status: str = "matched", kid: str | None = None, cand=None) -> tuple:
    return (key, key, 2001, kid, {"plot": "p"} if kid else None, status, 0.95, "score", cand,
            "DATA-01")  # fmt: skip


async def load(conn, works, locs, shoots) -> None:
    async with conn.transaction():
        await staging.reset(conn)
        await staging.write_stations(conn, [("101", "가", "1호선", 35.1, 129.0, None)])
        await staging.write_works(conn, works)
        await staging.write_locations(conn, locs)
        await staging.write_shootings(conn, shoots)
        await staging.publish(conn)


LOC_A = ("a", "A", "부산 중구 1", "중구", 35.1, 129.0, "address", "101", 1.0)
LOC_B = ("b", "B", None, None, None, None, "none", None, None)


def test_publish_keeps_ids_and_removes_missing() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        shoots = [("w1", "a", "DATA-01", {"x": 1}, None), ("w2", "b", "DATA-02", {}, None)]
        await load(conn, [work("w1", kid="K1"), work("w2", "unmatched")], [LOC_A, LOC_B], shoots)
        ids = dict(await conn.fetch("SELECT title_norm, id FROM work"))
        loc_a = await conn.fetchval("SELECT id FROM location WHERE name_norm = 'a'")
        assert await conn.fetchval("SELECT count(*) FROM shooting") == 2
        assert await conn.fetchval("SELECT nearest_station_id FROM location WHERE id=$1", loc_a)

        # 2회차: w2·b가 원본에서 빠지고, K1이 w3로 옮겨 감
        await load(
            conn,
            [work("w1", "review", cand={"kmdb_id": "K1"}), work("w3", kid="K1")],
            [LOC_A],
            [("w1", "a", "DATA-01", {}, None), ("w3", "a", "DATA-01", {}, None)],
        )
        now = dict(await conn.fetch("SELECT title_norm, id FROM work"))
        assert now["w1"] == ids["w1"] and "w2" not in now
        assert await conn.fetchval("SELECT id FROM location WHERE name_norm = 'a'") == loc_a
        assert await conn.fetchval("SELECT count(*) FROM location") == 1
        assert await conn.fetchval("SELECT kmdb_id FROM work WHERE title_norm='w3'") == "K1"
        assert await conn.fetchval("SELECT kmdb_id FROM work WHERE title_norm='w1'") is None
        assert await conn.fetchval("SELECT count(*) FROM shooting") == 2

    run(body)


def test_review_queue_not_duplicated_and_decisions_read_back() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        w = work("w1", "review", cand={"kmdb_id": "K9", "kmdb_title": "w1"})
        await load(conn, [w], [LOC_A], [("w1", "a", "DATA-01", {}, None)])
        await load(conn, [w], [LOC_A], [("w1", "a", "DATA-01", {}, None)])
        assert await conn.fetchval("SELECT count(*) FROM review_item") == 1
        assert await staging.review_decisions(conn) == {"w1": ("pending", "K9")}

        await conn.execute("UPDATE review_item SET status='approved', handled_at=now()")
        assert await staging.review_decisions(conn) == {"w1": ("approved", "K9")}
        assert (await staging.known_coords(conn))["a"] == ("부산 중구 1", 35.1, 129.0)

    run(body)


def test_pending_review_removed_when_work_no_longer_review() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        shoot = [("w1", "a", "DATA-01", {}, None)]
        await load(conn, [work("w1", "review", cand={"kmdb_id": "K9"})], [LOC_A], shoot)
        assert await conn.fetchval("SELECT count(*) FROM review_item") == 1
        await load(conn, [work("w1", kid="K9")], [LOC_A], shoot)
        assert await conn.fetchval("SELECT count(*) FROM review_item") == 0

    run(body)

"""검수 큐 쓰기 DB 테스트 (FR-608, BR-04).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다.
review_item을 비우므로 개발 DB를 쓰면 안 된다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_review_queue_db.py
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable

import asyncpg
import pytest

from app.batch.ai_review import ReviewProposal
from app.data import review_queue

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

TAG = ReviewProposal("tag", "location", 7, {"tags": ["해변"], "reason": "이름에 해수욕장"})
MATCH = ReviewProposal("match", "work", 31, {"verdict": "unsure", "candidate": None})


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("TRUNCATE review_item RESTART IDENTITY")
            await body(conn)
        finally:
            await conn.close()

    asyncio.run(main())


def test_adds_pending_ai_proposals() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        assert await review_queue.add_proposals(conn, [TAG, MATCH]) == 2

        rows = await conn.fetch(
            "SELECT kind, target_type, target_id, proposal::text, proposer, score, status "
            "FROM review_item ORDER BY id"
        )
        assert [(r["kind"], r["target_id"], r["proposer"], r["status"]) for r in rows] == [
            ("tag", 7, "ai", "pending"),
            ("match", 31, "ai", "pending"),
        ]
        assert json.loads(rows[0]["proposal"]) == TAG.proposal  # 한글 그대로
        assert rows[0]["score"] is None

    run(body)


def test_rerun_does_not_duplicate_pending_item() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await review_queue.add_proposals(conn, [TAG])

        assert await review_queue.add_proposals(conn, [TAG]) == 0
        assert await conn.fetchval("SELECT count(*) FROM review_item") == 1

    run(body)


def test_handled_item_allows_new_proposal() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await review_queue.add_proposals(conn, [TAG])
        await conn.execute("UPDATE review_item SET status = 'rejected'")

        assert await review_queue.add_proposals(conn, [TAG]) == 1

    run(body)


def test_pending_targets() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        rule = ReviewProposal("tag", "location", 9, {"tags": ["시장"]}, proposer="rule")
        await review_queue.add_proposals(conn, [TAG, MATCH, rule])

        assert await review_queue.pending_targets(conn, "tag") == {7}
        assert await review_queue.pending_targets(conn, "tag", proposer="rule") == {9}
        assert await review_queue.pending_targets(conn, "match") == {31}

    run(body)


def test_drop_stale_ai_matches_keeps_ones_with_rule_item() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        rule = ReviewProposal("match", "work", 31, {"kmdb_id": "K01"}, proposer="rule")
        lonely = ReviewProposal("match", "work", 32, {"verdict": "unsure", "kmdb_id": None})
        await review_queue.add_proposals(conn, [rule, MATCH, lonely])

        assert await review_queue.drop_stale_ai_matches(conn) == 1  # 32번은 규칙 항목이 없음
        assert await review_queue.pending_targets(conn, "match") == {31}

    run(body)


def test_untagged_locations() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await conn.execute("TRUNCATE location RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO location (name, name_norm, address, gu, tags) VALUES"
            " ('송정해수욕장', '송정해수욕장', '부산 해운대구', '해운대구', '{}'),"
            " ('자갈치시장', '자갈치시장', NULL, '중구', '{시장}')"
        )

        (place,) = await review_queue.untagged_locations(conn)

        assert (place.name, place.address, place.gu) == (
            "송정해수욕장",
            "부산 해운대구",
            "해운대구",
        )

    run(body)

"""호출 수·응답 캐시 DB 테스트 (NFR-018, AI-REQ-15).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다(usage_counter·ai_cache를 비운다).
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_ai_store_db.py
"""

import asyncio
import os
from datetime import date

import pytest

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

DAY = date(2026, 11, 17)


def run(body) -> None:
    import asyncpg

    from app.data.ai_gateway import PgAICache, PgUsageCounter

    async def main() -> None:
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)

        async def get_pool():
            return pool

        try:
            await pool.execute("TRUNCATE usage_counter, ai_cache")
            await body(pool, PgUsageCounter(get_pool), PgAICache(get_pool))
        finally:
            await pool.close()

    asyncio.run(main())


def test_reserve_stops_at_limit() -> None:
    async def body(pool, usage, cache) -> None:
        assert [await usage.reserve(DAY, "gemini", 2) for _ in range(3)] == [1, 2, None]
        assert await usage.reserve(DAY, "gemini", 0) is None
        assert [await usage.reserve(DAY, "ollama", None) for _ in range(2)] == [1, 2]
        assert await usage.counts(DAY) == {"gemini": 2, "ollama": 2}

    run(body)


def test_concurrent_reserve_never_exceeds_limit() -> None:
    async def body(pool, usage, cache) -> None:
        results = await asyncio.gather(*(usage.reserve(DAY, "gemini", 5) for _ in range(20)))

        assert sorted(r for r in results if r is not None) == [1, 2, 3, 4, 5]
        assert await usage.counts(DAY) == {"gemini": 5}

    run(body)


def test_cache_round_trip_and_expiry() -> None:
    async def body(pool, usage, cache) -> None:
        key = "a" * 64
        await cache.put(key, "extract_query", {"districts": ["해운대구"]}, 3600)
        assert await cache.get(key) == {"districts": ["해운대구"]}

        await cache.put(key, "extract_query", {"districts": []}, 3600)  # 덮어쓰기
        assert await cache.get(key) == {"districts": []}

        await cache.put("b" * 64, "work_intro", {"x": 1}, -1)  # 이미 만료
        assert await cache.get("b" * 64) is None
        assert await cache.purge_expired() == 1

    run(body)

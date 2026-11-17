"""호출 수(usage_counter)와 응답 캐시(ai_cache)의 PostgreSQL 구현 (ADR-013, COMP-12).

API 서버는 app.data.db의 연결 풀을 쓰고, 배치처럼 다른 프로세스는 자기 풀을 넘긴다.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

PoolGetter = Callable[[], Awaitable["asyncpg.Pool"]]

# 한도 안일 때만 1 올린다. 동시에 여러 요청이 와도 한 문장으로 처리돼 한도를 넘지 않는다.
_RESERVE = """
INSERT INTO usage_counter (day, key, count) VALUES ($1::date, $2::text, 1)
ON CONFLICT (day, key) DO UPDATE SET count = usage_counter.count + 1
WHERE $3::integer IS NULL OR usage_counter.count < $3::integer
RETURNING count
"""

_CACHE_GET = """
SELECT response::text FROM ai_cache WHERE key = $1::text AND expires_at > now()
"""

_CACHE_PUT = """
INSERT INTO ai_cache (key, task, response, expires_at)
VALUES ($1::text, $2::text, $3::jsonb, now() + make_interval(secs => $4::integer))
ON CONFLICT (key) DO UPDATE
SET task = EXCLUDED.task, response = EXCLUDED.response, expires_at = EXCLUDED.expires_at
"""


def _default_pool() -> PoolGetter:
    from app.data import db

    return db.get_pool


class PgUsageCounter:
    def __init__(self, pool: PoolGetter | None = None) -> None:
        self._pool = pool or _default_pool()

    async def reserve(self, day: date, key: str, limit: int | None) -> int | None:
        if limit is not None and limit <= 0:
            return None  # 한도 0: 이 공급자를 쓰지 않음(첫 행도 만들지 않는다)
        pool = await self._pool()
        return await pool.fetchval(_RESERVE, day, key, limit)

    async def counts(self, day: date) -> dict[str, int]:
        """관리자 리포트용 오늘 호출 수."""
        pool = await self._pool()
        rows = await pool.fetch("SELECT key, count FROM usage_counter WHERE day = $1::date", day)
        return {r["key"]: r["count"] for r in rows}


class PgAICache:
    def __init__(self, pool: PoolGetter | None = None) -> None:
        self._pool = pool or _default_pool()

    async def get(self, key: str) -> dict[str, Any] | None:
        pool = await self._pool()
        text = await pool.fetchval(_CACHE_GET, key)
        return None if text is None else json.loads(text)

    async def put(self, key: str, task: str, response: dict[str, Any], ttl_s: int) -> None:
        pool = await self._pool()
        await pool.execute(
            _CACHE_PUT, key, task, json.dumps(response, ensure_ascii=False), int(ttl_s)
        )

    async def purge_expired(self) -> int:
        """만료된 캐시를 지운다(배치에서 하루 한 번). 지운 수."""
        pool = await self._pool()
        status = await pool.execute("DELETE FROM ai_cache WHERE expires_at <= now()")
        return int(status.rsplit(" ", 1)[-1])

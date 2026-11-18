"""PostgreSQL 연결 풀 (COMP-12). DB에는 데이터 접근 계층만 접근한다(SDD 3.3)."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """처음 호출할 때 연결 풀을 만든다. DB가 늦게 떠도 다음 호출에서 다시 시도한다."""
    global _pool
    if _pool is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError("DATABASE_URL이 설정되지 않았습니다")
        pool = await asyncpg.create_pool(dsn=url, min_size=1, max_size=5)
        if _pool is None:
            _pool = pool
        else:  # 동시에 두 요청이 풀을 만든 경우 하나만 남긴다
            await pool.close()
    return _pool


class DatabaseUnavailable(Exception):
    """DB에 연결하지 못함(ERR-015). API는 503으로 알린다. 접속 정보는 메시지에 넣지 않는다."""


@asynccontextmanager
async def connection(timeout_s: float = 3.0) -> AsyncIterator[asyncpg.Connection]:
    """풀에서 연결 하나를 빌린다. 연결 단계의 실패만 DatabaseUnavailable로 바꾼다.

    질의 중 오류(SQL 문제 등)는 그대로 올라가 500으로 처리된다.
    """
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=timeout_s)
        conn = await pool.acquire(timeout=timeout_s)
    except (
        OSError,
        RuntimeError,
        asyncio.TimeoutError,
        asyncpg.PostgresError,
        asyncpg.InterfaceError,
    ):
        logger.warning("DB 연결 실패")
        raise DatabaseUnavailable from None
    try:
        yield conn
    finally:
        await pool.release(conn)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def ping(timeout_s: float = 3.0) -> bool:
    """DB가 응답하면 True. 접속 정보가 로그에 남지 않도록 예외 내용은 기록하지 않는다."""
    try:
        pool = await asyncio.wait_for(get_pool(), timeout=timeout_s)
        async with pool.acquire(timeout=timeout_s) as conn:
            return await conn.fetchval("SELECT 1", timeout=timeout_s) == 1
    except Exception:
        logger.warning("DB 연결 확인 실패")
        return False

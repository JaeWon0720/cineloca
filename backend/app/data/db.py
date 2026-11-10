"""PostgreSQL 연결 풀 (COMP-12). DB에는 데이터 접근 계층만 접근한다(SDD 3.3)."""

import asyncio
import logging

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

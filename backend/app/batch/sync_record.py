"""동기화 기록과 반영 결정 (FR-612·613, ADR-012). C1 배치(SyncJob)가 불러 쓴다.

사용 순서:
    run_id = await sync_runs.start(conn, "schedule")
    try:
        ... 스테이징에 수집·병합·매칭·지오코딩, counts·failures 채우기 ...
    except Exception as exc:
        await record_failure(conn, run_id, [failure("sync", exc)], counts)
        raise
    result = await guard_and_publish(conn, run_id, counts, failures, publish)

publish(conn)는 스테이징을 서비스 테이블로 교체하는 함수다. 가드를 통과할 때만,
상태 기록과 같은 트랜잭션 안에서 부른다. publish가 실패하면 교체도 기록도 되돌린 뒤
'failed'로 다시 기록하므로 서비스 데이터는 직전 그대로 남는다.
"""

import logging
from collections.abc import Awaitable, Callable

import asyncpg

from app.batch import guard
from app.batch.guard import GuardResult
from app.batch.report import GUARD_STEP, Failure, SyncCounts, failure
from app.data import sync_runs

logger = logging.getLogger(__name__)

Publish = Callable[[asyncpg.Connection], Awaitable[None]]


async def guard_and_publish(
    conn: asyncpg.Connection,
    run_id: int,
    counts: SyncCounts,
    failures: list[Failure],
    publish: Publish,
) -> GuardResult:
    """건수 가드를 통과하면 반영하고 'succeeded', 어기면 반영하지 않고 'guarded'로 남긴다."""
    prev = await sync_runs.last_succeeded_counts(conn)
    result = guard.check(counts, prev)
    try:
        async with conn.transaction():
            if result.passed:
                await publish(conn)
                status = "succeeded"
                recorded = failures
            else:
                status = "guarded"
                recorded = [failure(GUARD_STEP, r) for r in result.reasons] + failures
            await sync_runs.finish(conn, run_id, status, counts, prev, recorded)
    except Exception as exc:
        try:
            await record_failure(conn, run_id, [failure("publish", exc), *failures], counts, prev)
        except Exception:  # 기록마저 실패해도(DB 연결 끊김 등) 원래 오류를 올린다
            logger.warning("동기화 실패 기록을 남기지 못했습니다 (run_id=%s)", run_id)
        raise
    await sync_runs.prune(conn)
    return result


async def record_failure(
    conn: asyncpg.Connection,
    run_id: int,
    failures: list[Failure],
    counts: SyncCounts | None = None,
    prev: SyncCounts | None = None,
) -> None:
    """수집·병합 중 중단됐거나 반영에 실패한 동기화를 'failed'로 남긴다(ERR-013)."""
    if prev is None:
        prev = await sync_runs.last_succeeded_counts(conn)
    await sync_runs.finish(conn, run_id, "failed", counts, prev, failures)
    await sync_runs.prune(conn)

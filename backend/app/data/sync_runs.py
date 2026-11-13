"""sync_run 테이블 접근 (FR-612·613, ADR-012).

배치는 API 서버와 다른 프로세스라 연결을 직접 넘겨받는다(asyncpg.Connection).
"""

import json
from typing import Any

import asyncpg

from app.batch.report import (
    Failure,
    RunStatus,
    SyncCounts,
    SyncReport,
    build_report,
    limit_failures,
)

KEEP_RUNS = 10  # 최근 10회만 보관(SDD 3.5)


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return json.dumps([v.model_dump() for v in value], ensure_ascii=False)
    return json.dumps(value.model_dump(), ensure_ascii=False)


def _load_counts(raw: str | None) -> SyncCounts | None:
    return SyncCounts.model_validate_json(raw) if raw else None


async def start(conn: asyncpg.Connection, trigger: str) -> int:
    """실행 중 기록을 만들고 run_id를 돌려준다."""
    return await conn.fetchval(
        "INSERT INTO sync_run (trigger, status, started_at) VALUES ($1, 'running', now()) "
        "RETURNING id",
        trigger,
    )


async def last_succeeded_counts(conn: asyncpg.Connection) -> SyncCounts | None:
    """건수 가드가 비교할 직전 성공 동기화의 건수."""
    raw = await conn.fetchval(
        "SELECT counts::text FROM sync_run WHERE status = 'succeeded' AND counts IS NOT NULL "
        "ORDER BY finished_at DESC, id DESC LIMIT 1"
    )
    return _load_counts(raw)


async def finish(
    conn: asyncpg.Connection,
    run_id: int,
    status: RunStatus,
    counts: SyncCounts | None,
    prev_counts: SyncCounts | None,
    failures: list[Failure],
) -> None:
    await conn.execute(
        "UPDATE sync_run SET status = $2, counts = $3::jsonb, prev_counts = $4::jsonb, "
        "failures = $5::jsonb, finished_at = now() WHERE id = $1",
        run_id,
        status,
        _dump(counts),
        _dump(prev_counts),
        _dump(limit_failures(failures)),
    )


async def prune(conn: asyncpg.Connection, keep: int = KEEP_RUNS) -> int:
    """최근 keep회만 남긴다. 가드 비교 기준인 마지막 성공 기록은 오래돼도 지우지 않는다."""
    result = await conn.execute(
        """
        DELETE FROM sync_run
        WHERE id NOT IN (SELECT id FROM sync_run ORDER BY id DESC LIMIT $1)
          AND id IS DISTINCT FROM (
              SELECT id FROM sync_run WHERE status = 'succeeded'
              ORDER BY finished_at DESC, id DESC LIMIT 1)
        """,
        keep,
    )
    return int(result.split()[-1])


def _row_to_report(row: asyncpg.Record) -> SyncReport:
    failures = [Failure.model_validate(f) for f in json.loads(row["failures"] or "[]")]
    return build_report(
        run_id=row["id"],
        trigger=row["trigger"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        counts=_load_counts(row["counts"]),
        prev_counts=_load_counts(row["prev_counts"]),
        failures=failures,
    )


async def recent_reports(conn: asyncpg.Connection, limit: int = KEEP_RUNS) -> list[SyncReport]:
    """최근 동기화 리포트(최신 먼저). 관리자 조회 API(API-021)가 쓴다."""
    rows = await conn.fetch(
        "SELECT id, trigger, status, started_at, finished_at, counts::text AS counts, "
        "prev_counts::text AS prev_counts, failures::text AS failures "
        "FROM sync_run ORDER BY id DESC LIMIT $1",
        limit,
    )
    return [_row_to_report(r) for r in rows]

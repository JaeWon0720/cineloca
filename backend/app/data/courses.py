"""추천 코스 조회 (COMP-12 CourseRepository 일부, FR-508·614).

방문 인증의 완주 배지는 승인된 추천 코스만 대상으로 한다. 사용자가 만든 코스는 브라우저에만 있어
여기 없다(FR-306). 코스 생성·승인(API-024)은 F3 작업에서 더한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class CourseStops:
    id: int
    title: str
    location_ids: list[int]  # 방문 순서


async def approved_stops(conn: asyncpg.Connection, course_id: int) -> CourseStops | None:
    row = await conn.fetchrow(
        "SELECT id, title FROM course WHERE id = $1 AND status = 'approved'", course_id
    )
    if row is None:
        return None
    stops = await conn.fetch(
        "SELECT location_id FROM course_stop WHERE course_id = $1 ORDER BY ord", course_id
    )
    return CourseStops(row["id"], row["title"], [s["location_id"] for s in stops])


async def approved_titles(conn: asyncpg.Connection, ids: Sequence[int]) -> dict[int, str]:
    rows = await conn.fetch(
        "SELECT id, title FROM course WHERE id = ANY($1::int[]) AND status = 'approved'", list(ids)
    )
    return {r["id"]: r["title"] for r in rows}

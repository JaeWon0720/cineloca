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


# ── 추천 코스 목록·상세(FR-401·402, API-009·010) ─────────────
@dataclass(frozen=True)
class CourseRow:
    id: int
    theme: str  # work·person·district
    theme_ref: str | None
    title: str
    start_station_id: str | None
    start_location_id: int | None
    stops: list[tuple[int, int]]  # (촬영지 ID, 체류 분) 방문 순서


_COURSES = """
SELECT c.id, c.theme, c.theme_ref, c.title, c.start_station_id, c.start_location_id,
       coalesce(array_agg(s.location_id ORDER BY s.ord) FILTER (WHERE s.ord IS NOT NULL), '{}')
         AS location_ids,
       coalesce(array_agg(s.dwell_min ORDER BY s.ord) FILTER (WHERE s.ord IS NOT NULL), '{}')
         AS dwell
FROM course c LEFT JOIN course_stop s ON s.course_id = c.id
WHERE c.status = 'approved'
  AND ($1::int IS NULL OR c.id = $1)
  AND ($2::text IS NULL OR c.theme = $2)
GROUP BY c.id
ORDER BY c.theme, c.title COLLATE "C", c.id
"""


def _row(r: asyncpg.Record) -> CourseRow:
    return CourseRow(
        id=r["id"],
        theme=r["theme"],
        theme_ref=r["theme_ref"],
        title=r["title"],
        start_station_id=r["start_station_id"],
        start_location_id=r["start_location_id"],
        stops=list(zip(r["location_ids"], r["dwell"], strict=True)),
    )


async def approved_courses(conn: asyncpg.Connection, theme: str | None = None) -> list[CourseRow]:
    return [_row(r) for r in await conn.fetch(_COURSES, None, theme)]


async def approved_course(conn: asyncpg.Connection, course_id: int) -> CourseRow | None:
    rows = await conn.fetch(_COURSES, course_id, None)
    return _row(rows[0]) if rows else None

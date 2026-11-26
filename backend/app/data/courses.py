"""추천 코스 조회 (COMP-12 CourseRepository 일부, FR-508·614).

방문 인증의 완주 배지는 승인된 추천 코스만 대상으로 한다. 사용자가 만든 코스는 브라우저에만 있어
여기 없다(FR-306). 관리자 후보 생성·수정·승인(API-024, FR-614)은 아래 '관리자' 절에 있다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

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
    status: str = "approved"
    approved_at: datetime | None = None


_COURSES = """
SELECT c.id, c.theme, c.theme_ref, c.title, c.start_station_id, c.start_location_id,
       c.status, c.approved_at,
       coalesce(array_agg(s.location_id ORDER BY s.ord) FILTER (WHERE s.ord IS NOT NULL), '{}')
         AS location_ids,
       coalesce(array_agg(s.dwell_min ORDER BY s.ord) FILTER (WHERE s.ord IS NOT NULL), '{}')
         AS dwell
FROM course c LEFT JOIN course_stop s ON s.course_id = c.id
WHERE ($3::text IS NULL OR c.status = $3)
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
        status=r["status"],
        approved_at=r["approved_at"],
    )


async def approved_courses(conn: asyncpg.Connection, theme: str | None = None) -> list[CourseRow]:
    return [_row(r) for r in await conn.fetch(_COURSES, None, theme, "approved")]


async def approved_course(conn: asyncpg.Connection, course_id: int) -> CourseRow | None:
    rows = await conn.fetch(_COURSES, course_id, None, "approved")
    return _row(rows[0]) if rows else None


# ── 관리자: 후보 생성·수정·승인(API-024, FR-614) ──────────────
async def all_courses(
    conn: asyncpg.Connection, status: str | None = None, theme: str | None = None
) -> list[CourseRow]:
    """승인 전(draft)까지 모두. status·theme이 None이면 거르지 않는다."""
    return [_row(r) for r in await conn.fetch(_COURSES, None, theme, status)]


async def any_course(conn: asyncpg.Connection, course_id: int) -> CourseRow | None:
    rows = await conn.fetch(_COURSES, course_id, None, None)
    return _row(rows[0]) if rows else None


@dataclass(frozen=True)
class ThemePlace:
    id: int
    lat: float
    lon: float
    works: int  # 이 테마에 맞는 작품 수(많을수록 후보 우선)


_THEME_PLACES = {
    "work": """
        SELECT l.id, l.lat, l.lon, count(DISTINCT s.work_id) AS works
        FROM location l JOIN shooting s ON s.location_id = l.id
        WHERE l.lat IS NOT NULL AND s.work_id = $1::text::int
        GROUP BY l.id""",
    "person": """
        SELECT l.id, l.lat, l.lon, count(DISTINCT s.work_id) AS works
        FROM location l JOIN shooting s ON s.location_id = l.id
        JOIN work_person wp ON wp.work_id = s.work_id
        WHERE l.lat IS NOT NULL AND wp.person_id = $1::text::int
        GROUP BY l.id""",
    "district": """
        SELECT l.id, l.lat, l.lon, count(DISTINCT s.work_id) AS works
        FROM location l JOIN shooting s ON s.location_id = l.id
        WHERE l.lat IS NOT NULL AND l.gu = $1::text
        GROUP BY l.id""",
}


_SKIP = (
    " HAVING bool_and(NOT l.cert_excluded AND coalesce(l.caution, '') NOT IN"
    " ('private', 'residential') AND l.name !~ '^\\s*\\[(폐업|페업|철거)\\]')"
)


async def theme_places(conn: asyncpg.Connection, theme: str, ref: str) -> list[ThemePlace]:
    """테마에 맞는 좌표 있는 촬영지. 작품 수 많은 순, 같으면 ID 순.

    인증 제외·사유지·주거지는 후보에 넣지 않는다(관리자가 수정으로 넣을 수는 있음, FR-209).
    """
    rows = await conn.fetch(_THEME_PLACES[theme] + _SKIP + " ORDER BY works DESC, l.id", ref)
    return [ThemePlace(r["id"], r["lat"], r["lon"], r["works"]) for r in rows]


async def theme_name(conn: asyncpg.Connection, theme: str, ref: str) -> str | None:
    """작품 제목·인물 이름. 지역은 구·군 이름 그대로라 None을 돌려주지 않는다."""
    if theme == "district":
        return ref
    table, col = ("work", "title") if theme == "work" else ("person", "name")
    return await conn.fetchval(f"SELECT {col} FROM {table} WHERE id = $1::text::int", ref)


@dataclass(frozen=True)
class StopIn:
    location_id: int
    dwell_min: int
    leg_mode: str | None = None
    leg_min: float | None = None
    leg_line: str | None = None


async def save_course(
    conn: asyncpg.Connection,
    course_id: int | None,
    *,
    theme: str,
    theme_ref: str | None,
    title: str,
    start_station_id: str | None,
    stops: Sequence[StopIn],
    total_min: int,
    total_m: int,
) -> int:
    """새 후보를 넣거나(course_id=None) 있는 코스를 고친다. 고치면 승인 전(draft)으로 돌린다.

    방문 장소는 통째로 바꾼다. 한 트랜잭션 안에서 부른다.
    """
    if course_id is None:
        course_id = await conn.fetchval(
            "INSERT INTO course (theme, theme_ref, title, start_station_id, total_min, total_m)"
            " VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
            theme, theme_ref, title, start_station_id, total_min, total_m,
        )  # fmt: skip
    else:
        await conn.execute(
            "UPDATE course SET title = $2, start_station_id = $3, total_min = $4, total_m = $5,"
            " status = 'draft', approved_by = NULL, approved_at = NULL WHERE id = $1",
            course_id, title, start_station_id, total_min, total_m,
        )  # fmt: skip
        await conn.execute("DELETE FROM course_stop WHERE course_id = $1", course_id)
    await conn.executemany(
        "INSERT INTO course_stop (course_id, ord, location_id, dwell_min, leg_mode, leg_min,"
        " leg_line) VALUES ($1, $2, $3, $4, $5, $6, $7)",
        [
            (course_id, n, s.location_id, s.dwell_min, s.leg_mode, s.leg_min, s.leg_line)
            for n, s in enumerate(stops, start=1)
        ],
    )
    return course_id


async def set_approved(conn: asyncpg.Connection, course_id: int, by: str | None) -> None:
    """by가 있으면 승인, None이면 승인 취소(draft)."""
    await conn.execute(
        "UPDATE course SET status = CASE WHEN $2::text IS NULL THEN 'draft' ELSE 'approved' END,"
        " approved_by = $2, approved_at = CASE WHEN $2::text IS NULL THEN NULL ELSE now() END"
        " WHERE id = $1",
        course_id,
        by,
    )


async def delete_draft(conn: asyncpg.Connection, course_id: int) -> bool:
    status = await conn.execute("DELETE FROM course WHERE id = $1 AND status = 'draft'", course_id)
    return status.endswith(" 1")

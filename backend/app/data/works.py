"""작품 조회 (COMP-12 WorkRepository, F1 작품 FR-101~106·802, API-001·002).

- 모든 값은 바인딩 매개변수로 넘기고, 정렬은 허용 목록(_ORDER)에서만 고른다(ADR-010).
- 검색어는 정규화한 작품명(title_norm)과 감독·배우 이름에서 찾는다(FR-102).
- KMDb 저장본(work.kmdb)은 배치가 넣은 그대로 돌려준다. 꺼내 쓰는 일은 업무 계층이 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import asyncpg

MAX_CAST = 5  # FR-103: 주요 출연진 최대 5명

_ORDER = {
    "title": "w.title_norm, w.id",
    "year": "w.release_year DESC NULLS LAST, w.title_norm, w.id",
    "locations": "location_count DESC, w.release_year DESC NULLS LAST, w.id",
}
_COLS = """
w.id, w.title, w.release_year, w.genres, w.source_id, w.kmdb_id IS NOT NULL AS kmdb_matched,
(SELECT count(DISTINCT s.location_id) FROM shooting s WHERE s.work_id = w.id) AS location_count,
(SELECT string_agg(p.name, ', ' ORDER BY wp.ord, p.id) FROM work_person wp
   JOIN person p ON p.id = wp.person_id
  WHERE wp.work_id = w.id AND wp.role = 'director') AS director
"""
_WHERE = """
($1::text IS NULL OR strpos(w.title_norm, $1) > 0 OR EXISTS (
    SELECT 1 FROM work_person wp JOIN person p ON p.id = wp.person_id
    WHERE wp.work_id = w.id AND strpos(replace(lower(p.name), ' ', ''), $1) > 0))
AND ($2::text IS NULL OR $2 = ANY(w.genres))
"""
# 같은 촬영지나 같은 인물을 함께 가진 작품, 겹치는 수가 많은 순(FR-802)
_SIMILAR = """
WITH shared AS (
    SELECT DISTINCT s2.work_id AS id, 'l' AS kind, s2.location_id AS ref
    FROM shooting s1 JOIN shooting s2 ON s2.location_id = s1.location_id
    WHERE s1.work_id = $1 AND s2.work_id <> $1
    UNION ALL
    SELECT DISTINCT p2.work_id, 'p', p2.person_id
    FROM work_person p1 JOIN work_person p2 ON p2.person_id = p1.person_id
    WHERE p1.work_id = $1 AND p2.work_id <> $1
)
SELECT w.id, w.title, w.release_year
FROM shared JOIN work w ON w.id = shared.id
GROUP BY w.id ORDER BY count(*) DESC, w.release_year DESC NULLS LAST, w.id
LIMIT $2
"""


@dataclass(frozen=True)
class WorkQuery:
    q: str | None = None  # 정규화한 검색어. 없으면 None
    genre: str | None = None  # KMDb 장르 이름
    sort: str = "title"
    page: int = 1
    size: int = 20


@dataclass(frozen=True)
class WorkRow:
    id: int
    title: str
    release_year: int | None
    genres: list[str]
    director: str | None
    location_count: int
    kmdb_matched: bool
    source_id: str  # 작품을 처음 준 출처


@dataclass(frozen=True)
class WorkPage:
    total: int
    items: list[WorkRow]


@dataclass(frozen=True)
class WorkLocationRow:
    id: int
    name: str
    gu: str | None
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class WorkBriefRow:
    id: int
    title: str
    release_year: int | None


@dataclass(frozen=True)
class WorkDetailRow:
    work: WorkRow
    cast: list[str]
    kmdb: dict | None  # work.kmdb 저장본(STORE일 때만 있음)
    locations: list[WorkLocationRow]
    source_ids: set[str]  # 작품·촬영 기록의 출처


def _work(r: asyncpg.Record) -> WorkRow:
    return WorkRow(
        id=r["id"],
        title=r["title"],
        release_year=r["release_year"],
        genres=list(r["genres"]),
        director=r["director"],
        location_count=r["location_count"],
        kmdb_matched=r["kmdb_matched"],
        source_id=r["source_id"],
    )


async def page(conn: asyncpg.Connection, q: WorkQuery) -> WorkPage:
    if q.sort not in _ORDER:
        raise ValueError("sort는 title·year·locations 중 하나")
    total = await conn.fetchval(f"SELECT count(*) FROM work w WHERE {_WHERE}", q.q, q.genre)
    rows = await conn.fetch(
        f"SELECT {_COLS} FROM work w WHERE {_WHERE} ORDER BY {_ORDER[q.sort]} LIMIT $3 OFFSET $4",
        q.q,
        q.genre,
        q.size,
        (q.page - 1) * q.size,
    )
    return WorkPage(total, [_work(r) for r in rows])


async def get(conn: asyncpg.Connection, work_id: int) -> WorkDetailRow | None:
    r = await conn.fetchrow(f"SELECT {_COLS}, w.kmdb FROM work w WHERE w.id = $1", work_id)
    if r is None:
        return None
    cast = await conn.fetch(
        "SELECT p.name FROM work_person wp JOIN person p ON p.id = wp.person_id"
        " WHERE wp.work_id = $1 AND wp.role = 'actor' ORDER BY wp.ord, p.id LIMIT $2",
        work_id,
        MAX_CAST,
    )
    # 같은 촬영지가 출처 두 곳에 있으면 한 번만. 좌표 있는 곳 먼저(FR-210)
    shots = await conn.fetch(
        "SELECT l.id, l.name, l.gu, l.lat, l.lon, array_agg(s.source_id) AS source_ids"
        " FROM shooting s JOIN location l ON l.id = s.location_id WHERE s.work_id = $1"
        " GROUP BY l.id ORDER BY (l.lat IS NULL), l.name_norm, l.id",
        work_id,
    )
    kmdb = r["kmdb"]
    return WorkDetailRow(
        work=_work(r),
        cast=[c["name"] for c in cast],
        kmdb=json.loads(kmdb) if isinstance(kmdb, str) else kmdb,
        locations=[WorkLocationRow(s["id"], s["name"], s["gu"], s["lat"], s["lon"]) for s in shots],
        source_ids={r["source_id"]} | {i for s in shots for i in s["source_ids"]},
    )


async def similar(conn: asyncpg.Connection, work_id: int, n: int = 5) -> list[WorkBriefRow]:
    rows = await conn.fetch(_SIMILAR, work_id, n)
    return [WorkBriefRow(r["id"], r["title"], r["release_year"]) for r in rows]

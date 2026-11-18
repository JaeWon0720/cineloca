"""검색 템플릿 T1~T4 (COMP-12 SearchTemplates, FR-304, ADR-010).

조건에 맞는 템플릿 하나를 업무 계층(TemplateSearcher)이 고르고, 여기서는 정해 둔 SQL에 값만 넣는다.
- 질의문은 모듈을 읽을 때 한 번 만들어 둔 상수다. 값은 모두 바인딩 매개변수($n)로 넘기고,
  정렬은 템플릿마다 고정, 개수는 1~MAX_LIMIT으로 자른다. LLM 출력이 SQL 문장에 들어갈 길이 없다.
- 공통 CTE matched: 작품·인물·장르·구·군·연도 조건을 통과한 (촬영지, 작품) 쌍.
  조건이 없으면 None을 넘긴다. 좌표 없는 촬영지는 뺀다(FR-210).
- 장르는 KMDb 장르 이름으로 받는다(장르 묶음을 펼치는 일은 업무 계층이 한다).
- 기준점 좌표는 조회에만 쓰고 저장하지 않는다(BR-10).

| 템플릿 | 조건 | 정렬 |
| T1_FILTER | 기준 위치·이동 조건 없음 | 일치한 작품 수 많은 순 |
| T2_NEAR_POINT | 기준점 반경 안(earth_box로 GiST 색인, earth_distance로 다시 자름) | 가까운 순 |
| T3_NEAR_STATION | 역 미지정: station_walk_min ≤ N / 역 지정: station_time + station_walk_min ≤ N |
|                 | | 걸리는 시간 짧은 순 |
| T4_WORKS | 결과 유형이 작품 | 촬영지 수 많은 순 |
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg

MAX_LIMIT = 20  # FR-306


@dataclass(frozen=True)
class Conditions:
    """matched CTE의 매개변수 $1~$6. None은 '조건 없음'."""

    work_ids: Sequence[int] | None = None
    person_ids: Sequence[int] | None = None
    genres: Sequence[str] | None = None  # KMDb 장르
    gus: Sequence[str] | None = None
    year_from: int | None = None
    year_to: int | None = None

    def args(self) -> tuple[object, ...]:
        def arr(v: Sequence[object] | None) -> list[object] | None:
            return list(v) if v else None

        return (
            arr(self.work_ids),
            arr(self.person_ids),
            arr(self.genres),
            arr(self.gus),
            self.year_from,
            self.year_to,
        )


@dataclass(frozen=True)
class HitRow:
    id: int
    name: str
    gu: str | None
    lat: float
    lon: float
    station: str | None  # 가장 가까운 역 이름
    walk_min: float | None  # 그 역에서 도보 분
    work_count: int  # 조건에 맞는 작품 수
    source_ids: tuple[str, ...]  # 조건에 맞는 촬영 기록의 출처
    dist_m: float | None = None  # T2
    travel_min: float | None = None  # T3


@dataclass(frozen=True)
class HitPage:
    total: int  # limit 전 전체 수
    rows: list[HitRow]


@dataclass(frozen=True)
class WorkRow:
    id: int
    title: str
    release_year: int | None
    kmdb_matched: bool
    matches: bool = True  # 검색 조건을 통과한 작품인지


@dataclass(frozen=True)
class WorkHitRow:
    id: int
    title: str
    release_year: int | None
    location_count: int
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class WorkPage:
    total: int
    rows: list[WorkHitRow]


_MATCHED = """
WITH matched AS (
    SELECT s.location_id, s.work_id, s.source_id
    FROM shooting s
    JOIN work w     ON w.id = s.work_id
    JOIN location l ON l.id = s.location_id
    WHERE l.lat IS NOT NULL
      AND ($1::int[]  IS NULL OR w.id = ANY($1::int[]))
      AND ($2::int[]  IS NULL OR EXISTS (
            SELECT 1 FROM work_person wp
            WHERE wp.work_id = w.id AND wp.person_id = ANY($2::int[])))
      AND ($3::text[] IS NULL OR w.genres && $3::text[])
      AND ($4::text[] IS NULL OR l.gu = ANY($4::text[]))
      AND ($5::int    IS NULL OR w.release_year >= $5)
      AND ($6::int    IS NULL OR w.release_year <= $6)
)"""

_HITS = (
    _MATCHED
    + """,
agg AS (
    SELECT location_id, count(DISTINCT work_id) AS work_count,
           array_agg(DISTINCT source_id::text) AS source_ids
    FROM matched GROUP BY location_id
)
SELECT l.id, l.name, l.gu, l.lat, l.lon, l.station_walk_min, ns.name AS station,
       a.work_count, a.source_ids, count(*) OVER () AS total,"""
)
_FROM = """
FROM agg a
JOIN location l ON l.id = a.location_id
LEFT JOIN station ns ON ns.id = l.nearest_station_id"""

T1_FILTER = (
    _HITS
    + """
       NULL::float8 AS dist_m, NULL::float8 AS travel_min"""
    + _FROM
    + """
ORDER BY a.work_count DESC, l.id
LIMIT $7
"""
)

# $7 위도, $8 경도, $9 반경(m)
T2_NEAR_POINT = (
    _HITS
    + """
       earth_distance(ll_to_earth($7, $8), ll_to_earth(l.lat, l.lon)) AS dist_m,
       NULL::float8 AS travel_min"""
    + _FROM
    + """
WHERE earth_box(ll_to_earth($7, $8), $9) @> ll_to_earth(l.lat, l.lon)
  AND earth_distance(ll_to_earth($7, $8), ll_to_earth(l.lat, l.lon)) <= $9
ORDER BY dist_m, l.id
LIMIT $10
"""
)

# $7 출발 역(None이면 아무 역에서 걸어서), $8 분. 출발 역과 최근접 역이 같으면 열차 시간은 0
T3_NEAR_STATION = (
    _HITS
    + """
       NULL::float8 AS dist_m,
       (COALESCE(t.minutes, 0) + l.station_walk_min)::float8 AS travel_min"""
    + _FROM
    + """
LEFT JOIN station_time t ON t.from_id = $7::text AND t.to_id = l.nearest_station_id
WHERE l.nearest_station_id IS NOT NULL AND l.station_walk_min IS NOT NULL
  AND ($7::text IS NULL OR t.minutes IS NOT NULL OR l.nearest_station_id = $7::text)
  AND COALESCE(t.minutes, 0) + l.station_walk_min <= $8
ORDER BY travel_min, l.id
LIMIT $9
"""
)

T4_WORKS = (
    _MATCHED
    + """
SELECT w.id, w.title, w.release_year, count(DISTINCT m.location_id) AS location_count,
       array_agg(DISTINCT m.source_id::text) AS source_ids, count(*) OVER () AS total
FROM matched m
JOIN work w ON w.id = m.work_id
GROUP BY w.id
ORDER BY location_count DESC, w.release_year DESC NULLS LAST, w.id
LIMIT $7
"""
)

# 결과 촬영지($7)의 작품 목록. 조건에 맞는 작품 먼저, 최근 작품 순
WORKS_OF_HITS = (
    _MATCHED
    + """
SELECT s.location_id, w.id, w.title, w.release_year, w.kmdb_id IS NOT NULL AS kmdb_matched,
       EXISTS (SELECT 1 FROM matched m
               WHERE m.location_id = s.location_id AND m.work_id = s.work_id) AS matches
FROM (SELECT DISTINCT location_id, work_id FROM shooting WHERE location_id = ANY($7::int[])) s
JOIN work w ON w.id = s.work_id
ORDER BY s.location_id, matches DESC, w.release_year DESC NULLS LAST, w.id
"""
)

TEMPLATES: dict[str, str] = {
    "T1_FILTER": T1_FILTER,
    "T2_NEAR_POINT": T2_NEAR_POINT,
    "T3_NEAR_STATION": T3_NEAR_STATION,
    "T4_WORKS": T4_WORKS,
}

_POINT: dict[str, str] = {
    "station": "SELECT lat, lon FROM station WHERE id = $1",
    "location": "SELECT lat, lon FROM location WHERE id::text = $1 AND lat IS NOT NULL",
}

_NEAREST_STATION = """
SELECT id, earth_distance(ll_to_earth($1, $2), ll_to_earth(lat, lon)) AS dist_m
FROM station ORDER BY dist_m, id LIMIT 1
"""


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _page(rows: Sequence[asyncpg.Record]) -> HitPage:
    def num(v: object) -> float | None:
        return None if v is None else float(v)  # type: ignore[arg-type]

    hits = [
        HitRow(
            id=r["id"],
            name=r["name"],
            gu=r["gu"],
            lat=r["lat"],
            lon=r["lon"],
            station=r["station"],
            walk_min=num(r["station_walk_min"]),
            work_count=r["work_count"],
            source_ids=tuple(sorted(r["source_ids"])),
            dist_m=num(r["dist_m"]),
            travel_min=num(r["travel_min"]),
        )
        for r in rows
    ]
    return HitPage(rows[0]["total"] if rows else 0, hits)


async def t1_filter(conn: asyncpg.Connection, c: Conditions, limit: int) -> HitPage:
    return _page(await conn.fetch(T1_FILTER, *c.args(), _limit(limit)))


async def t2_near_point(
    conn: asyncpg.Connection, c: Conditions, lat: float, lon: float, radius_m: float, limit: int
) -> HitPage:
    rows = await conn.fetch(
        T2_NEAR_POINT, *c.args(), float(lat), float(lon), float(radius_m), _limit(limit)
    )
    return _page(rows)


async def t3_near_station(
    conn: asyncpg.Connection, c: Conditions, base_station: str | None, minutes: float, limit: int
) -> HitPage:
    rows = await conn.fetch(T3_NEAR_STATION, *c.args(), base_station, float(minutes), _limit(limit))
    return _page(rows)


async def t4_works(conn: asyncpg.Connection, c: Conditions, limit: int) -> WorkPage:
    rows = await conn.fetch(T4_WORKS, *c.args(), _limit(limit))
    hits = [
        WorkHitRow(
            r["id"],
            r["title"],
            r["release_year"],
            r["location_count"],
            tuple(sorted(r["source_ids"])),
        )
        for r in rows
    ]
    return WorkPage(rows[0]["total"] if rows else 0, hits)


async def works_of_hits(
    conn: asyncpg.Connection, c: Conditions, location_ids: Sequence[int]
) -> dict[int, list[WorkRow]]:
    found: dict[int, list[WorkRow]] = {i: [] for i in location_ids}
    for r in await conn.fetch(WORKS_OF_HITS, *c.args(), list(location_ids)):
        found[r["location_id"]].append(
            WorkRow(r["id"], r["title"], r["release_year"], r["kmdb_matched"], r["matches"])
        )
    return found


async def point(
    conn: asyncpg.Connection, kind: Literal["station", "location"], ref_id: str
) -> tuple[float, float] | None:
    """기준점 좌표(역 또는 좌표가 있는 촬영지). 질의문은 종류별로 정해 둔 것 중에서만 고른다."""
    r = await conn.fetchrow(_POINT[kind], ref_id)
    return (r["lat"], r["lon"]) if r else None


async def nearest_station(
    conn: asyncpg.Connection, lat: float, lon: float
) -> tuple[str, float] | None:
    """기준점에서 가장 가까운 역과 직선거리(m). '여기서 지하철로 N분'의 출발 역을 정할 때 쓴다."""
    r = await conn.fetchrow(_NEAREST_STATION, float(lat), float(lon))
    return (r["id"], float(r["dist_m"])) if r else None

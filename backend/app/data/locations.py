"""촬영지 조회 (COMP-12 LocationRepository, F2 지도 탐색 FR-201~210).

- 반경 검색은 cube·earthdistance 확장과 GiST 색인(location_geo_idx)으로 한다(ADR-011).
  earth_box로 색인을 타고, earth_distance로 원 밖(상자 모서리)을 걸러 낸다.
- 좌표 없는 촬영지는 검색에 나오지 않는다(FR-210).
- 모든 값은 바인딩 매개변수로 넘긴다(ADR-010). 기준 위치는 조회에만 쓰고 저장하지 않는다(BR-10).
- 작품 조건(장르·개봉연도)은 '한 작품이 모두 만족'하는 촬영지만 고른다(FR-205).
  장르는 KMDb 장르 이름으로 받는다. 장르 묶음을 펼치는 일은 업무 계층(MapService)이 한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import asyncpg


@dataclass(frozen=True)
class SearchQuery:
    lat: float
    lon: float
    radius_m: int
    genres: Sequence[str] = ()  # KMDb 장르. 비면 장르 조건 없음
    year_from: int | None = None
    year_to: int | None = None
    gu: Sequence[str] = ()
    limit: int = 200


@dataclass(frozen=True)
class StationRow:
    id: str
    name: str
    line: str
    walk_min: float | None


@dataclass(frozen=True)
class WorkRow:
    id: int
    title: str
    release_year: int | None
    kmdb_matched: bool
    matches: bool = True  # 검색 조건(장르·연도)을 만족하는 작품인지


@dataclass(frozen=True)
class HitRow:
    id: int
    name: str
    gu: str | None
    lat: float
    lon: float
    distance_m: float
    station: StationRow | None
    works: list[WorkRow] = field(default_factory=list)  # 조건에 맞는 작품 먼저, 최근 작품 순


@dataclass(frozen=True)
class SearchRows:
    total: int  # 반경·필터를 만족하는 전체 수(limit 전)
    hits: list[HitRow]
    source_ids: set[str]


@dataclass(frozen=True)
class PhotoRow:
    url: str
    title: str | None
    source_id: str


@dataclass(frozen=True)
class LocationRow:
    id: int
    name: str
    address: str | None
    gu: str | None
    lat: float | None
    lon: float | None
    tags: list[str]
    caution: str | None
    safety_note: str | None
    cert_radius_m: int
    cert_excluded: bool
    station: StationRow | None
    photos: list[PhotoRow]
    works: list[WorkRow]
    source_ids: set[str]


@dataclass(frozen=True)
class SourceRow:
    id: str
    org: str
    name: str
    url: str
    base_date: date | None


@dataclass(frozen=True)
class StationPoint:
    id: str
    name: str
    line: str
    lat: float
    lon: float


_SEARCH = """
WITH base AS (SELECT ll_to_earth($1, $2) AS e),
hits AS (
    SELECT l.id, l.name, l.gu, l.lat, l.lon, l.nearest_station_id, l.station_walk_min,
           earth_distance(b.e, ll_to_earth(l.lat, l.lon)) AS dist
    FROM location l CROSS JOIN base b
    WHERE l.lat IS NOT NULL
      AND earth_box(b.e, $3) @> ll_to_earth(l.lat, l.lon)
      AND earth_distance(b.e, ll_to_earth(l.lat, l.lon)) <= $3
      AND (cardinality($4::text[]) = 0 OR l.gu = ANY($4::text[]))
      AND EXISTS (
          SELECT 1 FROM shooting s JOIN work w ON w.id = s.work_id
          WHERE s.location_id = l.id
            AND (cardinality($5::text[]) = 0 OR w.genres && $5::text[])
            AND ($6::int IS NULL OR w.release_year >= $6)
            AND ($7::int IS NULL OR w.release_year <= $7)
      )
)
SELECT h.id, h.name, h.gu, h.lat, h.lon, h.dist, h.station_walk_min,
       st.id AS st_id, st.name AS st_name, st.line AS st_line,
       count(*) OVER () AS total
FROM hits h LEFT JOIN station st ON st.id = h.nearest_station_id
ORDER BY h.dist, h.id
LIMIT $8
"""

# 촬영지별 작품. 같은 작품이 출처 두 곳에 있으면 한 번만 센다.
_WORKS = """
SELECT s.location_id, w.id, w.title, w.release_year, w.kmdb_id IS NOT NULL AS kmdb_matched,
       ((cardinality($2::text[]) = 0 OR w.genres && $2::text[])
        AND ($3::int IS NULL OR w.release_year >= $3)
        AND ($4::int IS NULL OR w.release_year <= $4)) AS matches
FROM (SELECT DISTINCT location_id, work_id FROM shooting WHERE location_id = ANY($1::int[])) s
JOIN work w ON w.id = s.work_id
ORDER BY s.location_id, matches DESC, w.release_year DESC NULLS LAST, w.id
"""

_LOCATION = """
SELECT l.id, l.name, l.address, l.gu, l.lat, l.lon, l.tags, l.caution, l.safety_note,
       l.cert_radius_m, l.cert_excluded, l.station_walk_min,
       st.id AS st_id, st.name AS st_name, st.line AS st_line
FROM location l LEFT JOIN station st ON st.id = l.nearest_station_id
WHERE l.id = $1
"""


def _station(row: asyncpg.Record) -> StationRow | None:
    if row["st_id"] is None:
        return None
    walk = row["station_walk_min"]
    return StationRow(
        row["st_id"], row["st_name"], row["st_line"], None if walk is None else float(walk)
    )


async def _works_by_location(
    conn: asyncpg.Connection, ids: list[int], q: SearchQuery | None = None
) -> dict[int, list[WorkRow]]:
    genres = list(q.genres) if q else []
    year_from, year_to = (q.year_from, q.year_to) if q else (None, None)
    found: dict[int, list[WorkRow]] = {i: [] for i in ids}
    for r in await conn.fetch(_WORKS, ids, genres, year_from, year_to):
        found[r["location_id"]].append(
            WorkRow(r["id"], r["title"], r["release_year"], r["kmdb_matched"], r["matches"])
        )
    return found


async def _shooting_sources(conn: asyncpg.Connection, ids: list[int]) -> set[str]:
    rows = await conn.fetch(
        "SELECT DISTINCT source_id FROM shooting WHERE location_id = ANY($1::int[])", ids
    )
    return {r["source_id"] for r in rows}


async def search(conn: asyncpg.Connection, q: SearchQuery) -> SearchRows:
    rows = await conn.fetch(
        _SEARCH,
        q.lat,
        q.lon,
        float(q.radius_m),
        list(q.gu),
        list(q.genres),
        q.year_from,
        q.year_to,
        q.limit,
    )
    if not rows:
        return SearchRows(total=0, hits=[], source_ids=set())
    ids = [r["id"] for r in rows]
    works = await _works_by_location(conn, ids, q)
    hits = [
        HitRow(
            id=r["id"],
            name=r["name"],
            gu=r["gu"],
            lat=r["lat"],
            lon=r["lon"],
            distance_m=r["dist"],
            station=_station(r),
            works=works[r["id"]],
        )
        for r in rows
    ]
    return SearchRows(rows[0]["total"], hits, await _shooting_sources(conn, ids))


async def get(conn: asyncpg.Connection, location_id: int) -> LocationRow | None:
    r = await conn.fetchrow(_LOCATION, location_id)
    if r is None:
        return None
    photos = [
        PhotoRow(p["url"], p["title"], p["source_id"])
        for p in await conn.fetch(
            "SELECT url, title, source_id FROM photo WHERE location_id = $1 ORDER BY id",
            location_id,
        )
    ]
    works = (await _works_by_location(conn, [location_id]))[location_id]
    sources = await _shooting_sources(conn, [location_id]) | {p.source_id for p in photos}
    return LocationRow(
        id=r["id"],
        name=r["name"],
        address=r["address"],
        gu=r["gu"],
        lat=r["lat"],
        lon=r["lon"],
        tags=list(r["tags"]),
        caution=r["caution"],
        safety_note=r["safety_note"],
        cert_radius_m=r["cert_radius_m"],
        cert_excluded=r["cert_excluded"],
        station=_station(r),
        photos=photos,
        works=works,
        source_ids=sources,
    )


async def sources(conn: asyncpg.Connection, ids: Sequence[str]) -> list[SourceRow]:
    rows = await conn.fetch(
        "SELECT id, org, name, url, base_date FROM source WHERE id = ANY($1::text[]) ORDER BY id",
        list(ids),
    )
    return [SourceRow(r["id"], r["org"], r["name"], r["url"], r["base_date"]) for r in rows]


async def stations(conn: asyncpg.Connection) -> list[StationPoint]:
    rows = await conn.fetch("SELECT id, name, line, lat, lon FROM station ORDER BY line, id")
    return [StationPoint(r["id"], r["name"], r["line"], r["lat"], r["lon"]) for r in rows]


async def release_year_range(conn: asyncpg.Connection) -> tuple[int, int] | None:
    """촬영지가 있는 작품의 개봉연도 범위(필터 선택 목록, FR-205)."""
    r = await conn.fetchrow(
        "SELECT min(w.release_year) AS lo, max(w.release_year) AS hi FROM work w"
        " WHERE EXISTS (SELECT 1 FROM shooting s WHERE s.work_id = w.id)"
    )
    if r is None or r["lo"] is None:
        return None
    return r["lo"], r["hi"]


# ── 방문 인증(F5, COMP-07)이 쓰는 조회 ─────────────────────────
@dataclass(frozen=True)
class CertTarget:
    """방문 인증 판정에 필요한 값만. 사용자 위치는 여기에 들어오지 않는다(BR-10)."""

    id: int
    name: str
    lat: float | None
    lon: float | None
    caution: str | None
    cert_radius_m: int
    cert_excluded: bool
    gu: str | None = None
    nearest_station_id: str | None = None  # FR-606(C1이 계산)
    station_walk_min: float | None = None


@dataclass(frozen=True)
class StampCard:
    """스탬프북 한 칸(FR-507): 촬영지명과 작품(최근 작품 먼저)."""

    id: int
    name: str
    works: list[WorkRow]


async def cert_targets(conn: asyncpg.Connection, ids: Sequence[int]) -> dict[int, CertTarget]:
    rows = await conn.fetch(
        "SELECT id, name, lat, lon, caution, cert_radius_m, cert_excluded, gu,"
        " nearest_station_id, station_walk_min FROM location WHERE id = ANY($1::int[])",
        list(ids),
    )
    return {
        r["id"]: CertTarget(
            r["id"],
            r["name"],
            r["lat"],
            r["lon"],
            r["caution"],
            r["cert_radius_m"],
            r["cert_excluded"],
            r["gu"],
            r["nearest_station_id"],
            float(r["station_walk_min"]) if r["station_walk_min"] is not None else None,
        )
        for r in rows
    }


async def stamp_cards(conn: asyncpg.Connection, ids: Sequence[int]) -> dict[int, StampCard]:
    rows = await conn.fetch("SELECT id, name FROM location WHERE id = ANY($1::int[])", list(ids))
    found = [r["id"] for r in rows]
    works = await _works_by_location(conn, found) if found else {}
    return {r["id"]: StampCard(r["id"], r["name"], works[r["id"]]) for r in rows}

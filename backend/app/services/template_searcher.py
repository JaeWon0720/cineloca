"""F3 검색 템플릿 실행 (COMP-06·12 TemplateSearcher, FR-304·306, ADR-010).

확정된 조건(Filters)으로 템플릿 하나를 고르고 매개변수를 채운다. SQL은 app.data.search_templates에
정해 둔 것만 쓴다(AI-REQ-05).

| 기준 위치(anchor) | 이동 조건(travel) | 템플릿 |
| 없음 | (무시) | T1_FILTER |
| 아무 역(ref 없음) | N분(기본 10분) | T3_NEAR_STATION: 최근접 역에서 걸어서 N분 |
| 역·장소·현재 위치 | 걸어서 N분 / 없음 | T2_NEAR_POINT: 반경 = N × 4000/60 ÷ 1.3 (없으면 1km) |
| 역 | 도시철도 N분(기본 30분) | T3_NEAR_STATION: 그 역에서 열차 + 내려서 걷기 ≤ N |
| 장소·현재 위치 | 도시철도 N분 | T3: 기준점의 최근접 역에서 출발, 그 역까지 걷는 시간을 N에서 뺌 |

- 기준점: 장소면 그 촬영지 좌표, 역이면 역 좌표, 현재 위치면 요청으로 받은 base_point.
  좌표는 조회에만 쓰고 로그·응답에 남기지 않는다(BR-10).
- 결과 유형이 작품(T4)이면 search_works()를 쓴다. QuerySearcher.search()는 촬영지만 돌려줄 수
  있어(QueryHit) 결과 유형과 관계없이 촬영지를 돌려준다.
- 출처: 촬영 기록의 출처 + 좌표 출처(DATA-06) + 역 정보를 보여 주면 DATA-08 + KMDb 연결 작품이면
  DATA-04.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal

import asyncpg

from app.core.names import station_label
from app.core.places import DEFAULT_RADIUS_M
from app.data import db
from app.data import search_templates as repo
from app.services.map_service import KMDB_SOURCE, expand_genres
from app.services.query_service import (
    Filters,
    QueryHit,
    QueryInputError,
    Ref,
    SearchPage,
    WorkBrief,
)

WALK_M_PER_MIN = 4000 / 60  # 도보 시속 4km(build-plan 5장 도보 규칙)
DETOUR = 1.3  # 직선거리 → 걷는 거리
DEFAULT_WALK_MIN = 10  # '역 근처'처럼 분을 말하지 않았을 때
DEFAULT_SUBWAY_MIN = 30
WORKS_PER_HIT = 5
GEO_SOURCE = "DATA-06"
STATION_SOURCE = "DATA-08"

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]
Template = Literal["T1_FILTER", "T2_NEAR_POINT", "T3_NEAR_STATION"]


@dataclass(frozen=True)
class Plan:
    """고른 템플릿과, 기준점을 어디서 얻는지. 좌표 값은 담지 않는다."""

    template: Template
    point_from: Literal["station", "location", "current"] | None = None
    point_ref: str | None = None
    radius_m: float | None = None  # T2
    minutes: float | None = None  # T3
    base_station: str | None = None  # T3: 출발 역(없으면 아무 역)
    via_nearest_station: bool = False  # T3: 기준점의 최근접 역을 출발 역으로


def walk_radius_m(minutes: float) -> float:
    return minutes * WALK_M_PER_MIN / DETOUR


def walk_minutes(dist_m: float) -> float:
    return dist_m * DETOUR / WALK_M_PER_MIN


def plan(filters: Filters, *, has_base_point: bool) -> Plan:
    """조건 → 템플릿(FR-304 표). DB 없이 정해진다."""
    anchor, travel = filters.anchor, filters.travel
    minutes = travel.max_min if travel and travel.max_min else None
    if anchor is None:
        return Plan("T1_FILTER")
    if anchor.type == "current":
        if not has_base_point:  # QueryService가 미리 빼지만 필터 패널 검색도 같은 길을 쓴다
            return Plan("T1_FILTER")
        source: tuple[Literal["station", "location", "current"], str | None] = ("current", None)
    elif anchor.ref is None:
        if anchor.type == "station":  # 아무 역에서 걸어서 N분
            return Plan("T3_NEAR_STATION", minutes=minutes or DEFAULT_WALK_MIN)
        return Plan("T1_FILTER")
    else:
        source = ("station" if anchor.type == "station" else "location", anchor.ref.id)
    if travel and travel.mode == "subway":
        minutes = minutes or DEFAULT_SUBWAY_MIN
        if source[0] == "station":
            return Plan("T3_NEAR_STATION", minutes=minutes, base_station=source[1])
        return Plan("T3_NEAR_STATION", *source, minutes=minutes, via_nearest_station=True)
    radius = walk_radius_m(minutes) if minutes else float(DEFAULT_RADIUS_M)
    return Plan("T2_NEAR_POINT", *source, radius_m=radius)


def _ids(refs: tuple[Ref, ...]) -> list[int] | None:
    try:
        return [int(r.id) for r in refs] or None
    except ValueError:
        raise QueryInputError("조건 값이 올바르지 않아요") from None


def conditions(f: Filters) -> repo.Conditions:
    return repo.Conditions(
        work_ids=_ids(f.works),
        person_ids=_ids(f.persons),
        genres=expand_genres(f.genres) or None,
        gus=list(f.districts) or None,
        year_from=f.year_from,
        year_to=f.year_to,
    )


def _hit(row: repo.HitRow, works: list[repo.WorkRow]) -> QueryHit:
    shown = works[:WORKS_PER_HIT]
    has_station = row.station is not None and row.walk_min is not None
    sources = set(row.source_ids) | {GEO_SOURCE}
    if has_station:
        sources.add(STATION_SOURCE)
    if any(w.kmdb_matched for w in shown):
        sources.add(KMDB_SOURCE)
    return QueryHit(
        location_id=row.id,
        name=row.name,
        gu=row.gu,
        lat=row.lat,
        lon=row.lon,
        works=tuple(WorkBrief(w.title, w.release_year) for w in shown),
        station=station_label(row.station) if has_station else None,  # type: ignore[arg-type]
        walk_min=max(1, round(row.walk_min)) if has_station else None,  # type: ignore[arg-type]
        source_ids=tuple(sorted(sources)),
    )


class TemplateSearcher:
    def __init__(self, connect: Connect = db.connection) -> None:
        self._connect = connect

    async def search(
        self, filters: Filters, *, base_point: tuple[float, float] | None, limit: int
    ) -> SearchPage:
        p = plan(filters, has_base_point=base_point is not None)
        c = conditions(filters)
        async with self._connect() as conn:
            page = await self._run(conn, p, c, base_point, limit)
            if page is None or not page.rows:
                return SearchPage(0, ())
            works = await repo.works_of_hits(conn, c, [r.id for r in page.rows])
        return SearchPage(page.total, tuple(_hit(r, works[r.id]) for r in page.rows))

    async def search_works(self, filters: Filters, *, limit: int) -> repo.WorkPage:
        """T4_WORKS: 조건에 맞는 작품을 촬영지 수 많은 순으로. 기준 위치·이동 조건은 쓰지 않는다."""
        async with self._connect() as conn:
            return await repo.t4_works(conn, conditions(filters), limit)

    async def _run(
        self,
        conn: asyncpg.Connection,
        p: Plan,
        c: repo.Conditions,
        base_point: tuple[float, float] | None,
        limit: int,
    ) -> repo.HitPage | None:
        if p.template == "T1_FILTER":
            return await repo.t1_filter(conn, c, limit)
        point: tuple[float, float] | None = None
        if p.point_from == "current":
            point = base_point
        elif p.point_from is not None and p.point_ref is not None:
            point = await repo.point(conn, p.point_from, p.point_ref)
        if p.template == "T2_NEAR_POINT":
            if point is None or p.radius_m is None:  # 기준점이 없어진 경우(동기화로 삭제 등)
                return None
            return await repo.t2_near_point(conn, c, point[0], point[1], p.radius_m, limit)
        minutes, base_station = p.minutes or 0.0, p.base_station
        if p.via_nearest_station:
            near = await repo.nearest_station(conn, *point) if point else None
            if near is None:
                return None
            base_station, minutes = near[0], minutes - walk_minutes(near[1])
            if minutes <= 0:  # 역까지 걷는 데 시간을 다 쓴다
                return None
        return await repo.t3_near_station(conn, c, base_station, minutes, limit)

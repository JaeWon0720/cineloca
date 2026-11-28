"""F2 지도 탐색 업무 로직 (COMP-04 MapService, FR-201~210, API-005~007).

- 장르 묶음 → KMDb 장르로 펼쳐 조회한다(app/core/genres.py). KMDb에 연결되지 않은 작품은
  장르가 없어서 장르 조건을 고르면 나오지 않는다(화면 안내 대상).
- 지도 결과는 거리순 최대 SEARCH_LIMIT곳. total은 상한 전 전체 수다.
- 방문 인증 가능 여부(BR-06·07)와 관람 주의 안내(FR-209)를 정한다.
- 출처 카드(FR-704): 보여 주는 촬영 기록·사진의 출처, KMDb 연결 작품이 있으면 KMDb.
- 결과는 API 응답 형식의 dict로 돌려준다(라우터가 응답 모델로 검증한다).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

import asyncpg

from app.core.genres import GENRE_GROUPS
from app.core.places import (
    BUSAN_STATION,
    DEFAULT_RADIUS_M,
    DISTRICT_CENTERS,
    RADIUS_OPTIONS,
    kakao_directions_url,
)
from app.data import db, dictionary
from app.data import locations as repo

SEARCH_LIMIT = 200  # 반경 3km 최다 165곳(임시 데이터, 남포역) → 마커 클러스터로 묶어 보여 준다
WORKS_PER_HIT = 3  # 요약 카드의 작품 수(FR-206)
KMDB_SOURCE = "DATA-04"

# FR-209 '외부에서만 관람' 안내
CAUTION_NOTICES = {
    "private": "사유지예요. 외부에서만 관람해 주세요.",
    "residential": "주민이 사는 곳이에요. 외부에서만 관람해 주세요.",
    "school": "학교예요. 외부에서만 관람해 주세요.",
}

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]


def expand_genres(groups: Iterable[str]) -> list[str]:
    """장르 묶음 이름 → 그 묶음에 속하는 KMDb 장르(중복 없이, 정렬)."""
    return sorted({g for group in groups for g in GENRE_GROUPS[group]})


CLOSED_PREFIX = re.compile(
    r"^\s*\[(?:폐업|페업|철거)\]"
)  # 장소명 앞 표시: 지금은 없어진 곳(페업은 원본 오타)


def is_closed(name: str | None) -> bool:
    """[폐업]·[철거]로 시작하는 촬영지. 가도 그 장소가 없어 방문 인증을 하지 않는다(BR-06)."""
    return bool(name and CLOSED_PREFIX.match(name))


def cert_info(
    located: bool, caution: str | None, excluded: bool, radius_m: int, name: str | None = None
) -> dict[str, Any]:
    """방문 인증 가능 여부(BR-06). 제외 이유: 좌표 없음 → 없어진 곳 → 주의 장소 → 관리자 제외."""
    reason = (
        "no_coordinates"
        if not located
        else "closed"
        if is_closed(name)
        else caution
        if caution in CAUTION_NOTICES
        else "excluded"
        if excluded
        else None
    )
    return {"eligible": reason is None, "radius_m": radius_m, "excluded_reason": reason}


def _gu(value: str | None) -> str | None:
    return value if value in DISTRICT_CENTERS else None  # BR-15 16개 구·군 밖 값은 비움


def _station(s: repo.StationRow | None) -> dict[str, Any] | None:
    if s is None or s.walk_min is None:
        return None
    return {"id": s.id, "name": s.name, "line": s.line, "walk_min": round(s.walk_min, 1)}


def _work(w: repo.WorkRow) -> dict[str, Any]:
    return {"id": w.id, "title": w.title, "release_year": w.release_year}


def _source_card(s: repo.SourceRow) -> dict[str, Any]:
    return {"id": s.id, "provider": s.org, "name": s.name, "base_date": s.base_date, "url": s.url}


PERSON_LIMIT = 10  # 인물 후보 최대 수(API-026)


class MapService:
    def __init__(self, connect: Connect = db.connection) -> None:
        self._connect = connect

    async def search(
        self,
        *,
        lat: float,
        lon: float,
        radius_m: int,
        genres: Sequence[str] = (),
        year_from: int | None = None,
        year_to: int | None = None,
        gu: Sequence[str] = (),
    ) -> dict[str, Any]:
        """API-005. 기준 위치(lat·lon)는 조회에만 쓰고 응답·로그에 남기지 않는다(BR-10)."""
        q = repo.SearchQuery(
            lat=lat,
            lon=lon,
            radius_m=radius_m,
            genres=expand_genres(genres),
            year_from=year_from,
            year_to=year_to,
            gu=list(gu),
            limit=SEARCH_LIMIT,
        )
        async with self._connect() as conn:
            rows = await repo.search(conn, q)
            source_ids = set(rows.source_ids)
            if any(w.kmdb_matched for h in rows.hits for w in h.works[:WORKS_PER_HIT]):
                source_ids.add(KMDB_SOURCE)
            cards = await repo.sources(conn, sorted(source_ids))
        items = [
            {
                "id": h.id,
                "name": h.name,
                "gu": _gu(h.gu),
                "lat": h.lat,
                "lon": h.lon,
                "distance_m": round(h.distance_m),
                "works": [_work(w) for w in h.works[:WORKS_PER_HIT]],
                "work_count": len(h.works),
                "nearest_station": _station(h.station),
                "directions_url": kakao_directions_url(h.name, h.lat, h.lon),
            }
            for h in rows.hits
        ]
        return {
            "radius_m": radius_m,
            "total": rows.total,
            "items": items,
            "sources": [_source_card(s) for s in cards],
        }

    async def location(self, location_id: int) -> dict[str, Any] | None:
        """API-006. 없는 id면 None."""
        async with self._connect() as conn:
            row = await repo.get(conn, location_id)
            if row is None:
                return None
            source_ids = set(row.source_ids)
            if any(w.kmdb_matched for w in row.works):
                source_ids.add(KMDB_SOURCE)
            cards = await repo.sources(conn, sorted(source_ids))
        located = row.lat is not None and row.lon is not None
        caution = row.caution if row.caution in CAUTION_NOTICES else None
        return {
            "id": row.id,
            "name": row.name,
            "address": row.address,
            "gu": _gu(row.gu),
            "located": located,
            "lat": row.lat,
            "lon": row.lon,
            "tags": row.tags,
            "photos": [
                {"url": p.url, "title": p.title, "source_id": p.source_id} for p in row.photos
            ],
            "nearest_station": _station(row.station),
            "caution": caution,
            "caution_notice": CAUTION_NOTICES.get(caution) if caution else None,
            "safety_note": row.safety_note,
            "cert": cert_info(located, caution, row.cert_excluded, row.cert_radius_m, row.name),
            "works": [{**_work(w), "kmdb_matched": w.kmdb_matched} for w in row.works],
            "directions_url": kakao_directions_url(row.name, row.lat, row.lon) if located else None,
            "sources": [_source_card(s) for s in cards],
        }

    async def persons(self, q: str, limit: int = PERSON_LIMIT) -> list[dict[str, Any]]:
        """API-026 인물 후보(FR-310 필터 패널). F3 사전 대조와 같은 이름 찾기(정확 일치·트라이그램).

        동명이인은 대표 작품(가장 최근 작품 제목)으로 구분한다(CH-24).
        """
        async with self._connect() as conn:
            matches = await dictionary.lookup(conn, "person", q, limit)
            ids = list(dict.fromkeys(m.canonical_id for m in matches))
            targets = await dictionary.describe(conn, "person", ids) if ids else {}
        return [
            {"id": int(t.id), "name": t.name, "work": t.detail}
            for i in ids
            if (t := targets.get(i)) is not None
        ]

    async def meta(self) -> dict[str, Any]:
        """API-007. 고정값(기본 위치·반경·구·군)과 DB 값(역·개봉연도 범위)."""
        async with self._connect() as conn:
            stations = await repo.stations(conn)
            years = await repo.release_year_range(conn)
        label, lat, lon = BUSAN_STATION
        return {
            "default_base": {"label": label, "lat": lat, "lon": lon},
            "radius_options": list(RADIUS_OPTIONS),
            "default_radius_m": DEFAULT_RADIUS_M,
            "districts": [
                {"name": n, "lat": a, "lon": o} for n, (a, o) in DISTRICT_CENTERS.items()
            ],
            "stations": [
                {"id": s.id, "name": s.name, "line": s.line, "lat": s.lat, "lon": s.lon}
                for s in stations
            ],
            "genres": list(GENRE_GROUPS),
            "year_range": {"min": years[0], "max": years[1]} if years else None,
        }

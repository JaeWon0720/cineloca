"""코스 주변 시설 추천 조립 (COMP-05 CourseService.recommend_amenities, API-012, FR-414~418, SD-03).

  1 촬영지(방문 순서) 확인 — 없는 곳 404, 좌표 없는 곳 409
  2 후보 검색(AmenityFinder, FR-414): 구간 중간 지점 1km 음식점, 마지막 촬영지 1km 숙박
  3 조건 구조화·선정·추천 이유(FacilityService, FR-415~418)
후보 검색 결과(AmenitySearch)를 선정 입력(FacilitySegment)으로 바꾸는 일을 여기서 한다.
TourAPI 응답·조건 문장 원문은 저장하지 않는다(NO_STORE, 마스킹본만 요청 로그).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.places import kakao_directions_url
from app.data import db
from app.data import locations as location_repo
from app.data.ai_gateway import LLMClient
from app.data.ai_gateway.tasks import FacilityConditions
from app.services.amenity_finder import AmenityFinder, AmenitySearch, Stop
from app.services.facility_service import (
    FacilityCandidate,
    FacilityResult,
    FacilitySegment,
    FacilityService,
)
from app.services.map_service import Connect

LODGING_KEY = "last"
TOURAPI_SOURCE = {
    "id": "DATA-13",
    "provider": "한국관광공사",
    "name": "TourAPI 위치기반 관광정보(음식점·숙박)",
    "base_date": None,
    "url": "https://api.visitkorea.or.kr",
}


class LocationNotFound(Exception):
    pass


class LocationNotLocated(Exception):
    """좌표가 없는 촬영지는 코스에 넣을 수 없다(FR-403·210)."""


def _segment_key(search: AmenitySearch) -> str:
    return LODGING_KEY if search.kind == "lodging" else f"{search.index + 1}-{search.index + 2}"


def to_segments(searches: Sequence[AmenitySearch], names: dict[int, str]) -> list[FacilitySegment]:
    """AmenitySearch(#80) → FacilitySegment(#85). 조회 실패는 failed로 넘겨 0건과 구분한다."""
    out = []
    for s in searches:
        a = names[s.from_location_id]
        if s.kind == "segment":
            b = names[s.to_location_id]  # type: ignore[index]
            anchor, stop_names = f"{a}~{b} 사이", (a, b)
        else:
            anchor, stop_names = f"{a} 근처", (a,)
        out.append(
            FacilitySegment(
                key=_segment_key(s),
                kind="food" if s.kind == "segment" else "lodging",
                anchor=anchor,
                stop_names=stop_names,
                candidates=tuple(
                    FacilityCandidate(
                        id=c.content_id,
                        name=c.name,
                        kind=c.facility_type,
                        category=c.category,
                        lat=c.lat,
                        lon=c.lon,
                        distance_m=c.distance_m,
                        address=c.address,
                    )
                    for c in s.candidates
                ),
                failed=s.status == "failed",
            )
        )
    return out


class AmenityRecommendService:
    def __init__(
        self, client: LLMClient, finder: AmenityFinder, connect: Connect = db.connection
    ) -> None:
        self._facility = FacilityService(client)
        self._finder = finder
        self._connect = connect

    async def recommend(
        self,
        location_ids: Sequence[int],
        *,
        text: str | None = None,
        selected: FacilityConditions | None = None,
    ) -> dict[str, Any]:
        """API-012. location_ids는 방문 순서(중복은 앞의 것만)."""
        ids = list(dict.fromkeys(location_ids))
        async with self._connect() as conn:
            targets = await location_repo.cert_targets(conn, ids)
        if any(i not in targets for i in ids):
            raise LocationNotFound
        if any(targets[i].lat is None or targets[i].lon is None for i in ids):
            raise LocationNotLocated
        stops = [Stop(i, targets[i].lat, targets[i].lon) for i in ids]  # type: ignore[arg-type]
        searches = await self._finder.find(stops)
        names = {i: targets[i].name for i in ids}
        result = await self._facility.recommend(
            to_segments(searches, names), text=text, selected=selected
        )
        return _response(result, searches)


def _response(result: FacilityResult, searches: Sequence[AmenitySearch]) -> dict[str, Any]:
    orders = {
        _segment_key(s): (s.index + 1, s.index + 2 if s.kind == "segment" else None)
        for s in searches
    }
    cond = result.conditions
    segments = []
    for seg in result.segments:
        from_order, to_order = orders[seg.key]
        segments.append(
            {
                "key": seg.key,
                "kind": seg.kind,
                "from_order": from_order,
                "to_order": to_order,
                "anchor": seg.anchor,
                "notice": seg.notice,
                "picks": [
                    {
                        "content_id": p.facility.id,
                        "name": p.facility.name,
                        "category": p.facility.category,
                        "address": p.facility.address,
                        "lat": p.facility.lat,
                        "lon": p.facility.lon,
                        "distance_m": p.facility.distance_m,
                        "matched": list(p.matched),
                        "reason": p.reason,
                        "ai_generated": p.ai_reason,  # FR-707 'AI 생성' 표시
                        "directions_url": kakao_directions_url(
                            p.facility.name, p.facility.lat, p.facility.lon
                        ),
                    }
                    for p in seg.picks
                ],
            }
        )
    return {
        "conditions": {
            "status": cond.status,
            "kinds": list(cond.conditions.kinds),
            "food": list(cond.conditions.food),
            "lodging": list(cond.conditions.lodging),
            "keywords": list(cond.conditions.keywords),
            "masked": cond.masked,
        },
        "notices": list(dict.fromkeys([*cond.notices, *result.notices])),
        "segments": segments,
        "sources": [TOURAPI_SOURCE],
    }

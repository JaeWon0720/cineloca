"""F2 API 테스트용 가짜 MapService. DB 없이 라우터·응답 형식·오류 처리만 확인한다."""

from typing import Any

from app.core.genres import GENRE_GROUPS
from app.core.places import kakao_directions_url
from app.services.map_service import CAUTION_NOTICES

SOURCE = {
    "id": "DATA-01",
    "provider": "부산영상위원회",
    "name": "영화 속 부산정보",
    "base_date": None,
    "url": "https://www.data.go.kr/data/15164328/openapi.do",
}
WORKS = [
    {"id": 11, "title": "해운대", "release_year": 2009},
    {"id": 12, "title": "도마뱀", "release_year": 2006},
    {"id": 13, "title": "쓰리 썸머 나잇", "release_year": 2015},
]


class FakeMapService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        hit = {
            "id": 1,
            "name": "해운대해수욕장",
            "gu": "해운대구",
            "lat": 35.160235,
            "lon": 129.159397,
            "distance_m": 160,
            "works": WORKS,
            "work_count": 4,
            "nearest_station": {"id": "203", "name": "해운대", "line": "2호선", "walk_min": 9.5},
            "directions_url": kakao_directions_url("해운대해수욕장", 35.160235, 129.159397),
        }
        return {"radius_m": kwargs["radius_m"], "total": 1, "items": [hit], "sources": [SOURCE]}

    async def location(self, location_id: int) -> dict[str, Any] | None:
        if location_id != 4:
            return None
        return {
            "id": 4,
            "name": "학장동 주택가",
            "address": None,
            "gu": "사상구",
            "located": False,
            "lat": None,
            "lon": None,
            "tags": [],
            "photos": [],
            "nearest_station": None,
            "caution": "residential",
            "caution_notice": CAUTION_NOTICES["residential"],
            "safety_note": None,
            "cert": {"eligible": False, "radius_m": 100, "excluded_reason": "no_coordinates"},
            "works": [{**WORKS[0], "kmdb_matched": True}],
            "directions_url": None,
            "sources": [SOURCE],
        }

    async def meta(self) -> dict[str, Any]:
        return {
            "default_base": {"label": "부산역", "lat": 35.115049, "lon": 129.041193},
            "radius_options": [500, 1000, 3000],
            "default_radius_m": 1000,
            "districts": [{"name": "중구", "lat": 35.106811, "lon": 129.032673}],
            "stations": [],
            "genres": list(GENRE_GROUPS),
            "year_range": {"min": 2000, "max": 2025},
        }

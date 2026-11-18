"""코스 주변 시설 후보 검색 (COMP-05 AmenityFinder, FR-414·418, SDD 2.3).

- 구간: 코스에서 이웃한 두 촬영지 사이(n곳 코스는 n−1개). 중간 지점 = 두 촬영지 위경도의 중간점.
- 구간마다 중간 지점 반경 1km 음식점(카페 포함), 마지막 촬영지 반경 1km 숙박을 찾는다.
- 요청 1건에 최대 10회(9구간 + 숙박 1회)를 동시에 보내고, 호출당 3초가 지나면 그 검색만 failed.
  실패·0건은 상태로만 알리고 코스는 그대로 둔다(FR-418, SDD 오류 표).
- 출발지가 현재 위치여도 그 좌표는 쓰지 않는다(구간은 촬영지끼리만).
- 응답은 저장하지 않는다(NO_STORE).

결과(AmenitySearch·AmenityCandidate)는 조건 구조화·선정(FR-415·416)의 입력이다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.core.amenities import CONTENT_TYPE, FacilityType, category_of, is_excluded
from app.core.geo import haversine_m
from app.data.tourapi import TIMEOUT_S, Place, TourApiClient, TourApiError

logger = logging.getLogger(__name__)

RADIUS_M = 1000  # FR-414(착수 PoC p7: 1km에서 0건 구간 5%)
MAX_STOPS = 10  # FR-403
SOURCE_ID = "DATA-13"
CALL_TIMEOUT_S = TIMEOUT_S  # 호출당 3초(SDD 2.3)

SearchKind = Literal["segment", "lodging"]
SearchStatus = Literal["ok", "empty", "failed"]


@dataclass(frozen=True)
class Stop:
    """코스의 촬영지 하나(방문 순서대로)."""

    location_id: int
    lat: float
    lon: float


@dataclass(frozen=True)
class AmenityCandidate:
    """시설 후보 하나. 선정(FR-416)은 이 목록 안에서만 고른다(후보에 없는 곳은 추천 안 함)."""

    content_id: str  # TourAPI contentid(화면·추천 이유에서 이 값으로 가리킴)
    facility_type: FacilityType
    category: str | None  # 밥·카페 / 호텔·콘도·레지던스·…. 모르면 None
    lcls_code: str | None
    name: str
    address: str | None
    lat: float
    lon: float
    distance_m: int  # 검색 중심(구간 중간 지점 또는 마지막 촬영지)에서 직선거리
    source_id: str = SOURCE_ID


@dataclass(frozen=True)
class AmenitySearch:
    """검색 한 번의 결과.

    segment: index i = i번째 촬영지와 i+1번째 촬영지 사이(0부터), 음식점(카페 포함)
    lodging: index = 마지막 촬영지 순번, 숙박
    """

    kind: SearchKind
    index: int
    from_location_id: int
    to_location_id: int | None  # lodging은 None
    status: SearchStatus
    candidates: list[AmenityCandidate] = field(default_factory=list)  # 가까운 순
    error: str | None = None  # failed일 때 종류(timeout·http·format·result·no_key), 로그용


def midpoint(a: Stop, b: Stop) -> tuple[float, float]:
    return (a.lat + b.lat) / 2, (a.lon + b.lon) / 2


class AmenityFinder:
    def __init__(self, client: TourApiClient, radius_m: int = RADIUS_M) -> None:
        self._client = client
        self._radius = radius_m

    async def find(self, stops: Sequence[Stop]) -> list[AmenitySearch]:
        """방문 순서대로 놓인 촬영지 2~10곳 → 구간 검색 n−1개 + 숙박 검색 1개(이 순서)."""
        if not 2 <= len(stops) <= MAX_STOPS:
            raise ValueError(f"촬영지는 2~{MAX_STOPS}곳이어야 합니다")
        jobs: list[tuple[SearchKind, int, Stop, Stop | None, tuple[float, float], FacilityType]] = [
            ("segment", i, a, b, midpoint(a, b), "food")
            for i, (a, b) in enumerate(zip(stops[:-1], stops[1:], strict=True))
        ]
        last = stops[-1]
        jobs.append(("lodging", len(stops) - 1, last, None, (last.lat, last.lon), "lodging"))
        return list(await asyncio.gather(*(self._search(*job) for job in jobs)))

    async def _search(
        self,
        kind: SearchKind,
        index: int,
        a: Stop,
        b: Stop | None,
        center: tuple[float, float],
        facility: FacilityType,
    ) -> AmenitySearch:
        base = {
            "kind": kind,
            "index": index,
            "from_location_id": a.location_id,
            "to_location_id": b.location_id if b else None,
        }
        try:
            # 호출 전체를 3초로 묶는다(httpx 제한은 연결·읽기 단계마다라 넘을 수 있음, #80 리뷰)
            places = await asyncio.wait_for(
                self._client.nearby(center[0], center[1], self._radius, CONTENT_TYPE[facility]),
                timeout=CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("주변 시설 조회 실패 provider=tourapi error=timeout")
            return AmenitySearch(**base, status="failed", error="timeout")
        except TourApiError as e:
            logger.warning("주변 시설 조회 실패 provider=tourapi error=%s", e.kind)
            return AmenitySearch(**base, status="failed", error=e.kind)
        candidates = _candidates(places, facility, center, self._radius)
        return AmenitySearch(**base, status="ok" if candidates else "empty", candidates=candidates)


def _candidates(
    places: Sequence[Place], facility: FacilityType, center: tuple[float, float], radius_m: int
) -> list[AmenityCandidate]:
    seen: set[str] = set()
    out: list[AmenityCandidate] = []
    for p in places:
        if p.content_id in seen or is_excluded(p.lcls_code):
            continue
        dist = haversine_m(
            center[0], center[1], p.lat, p.lon
        )  # 응답의 dist 대신 직접 계산(검증 가능)
        if dist > radius_m:
            continue
        seen.add(p.content_id)
        out.append(
            AmenityCandidate(
                content_id=p.content_id,
                facility_type=facility,
                category=category_of(facility, p.lcls_code),
                lcls_code=p.lcls_code,
                name=p.title,
                address=p.address,
                lat=p.lat,
                lon=p.lon,
                distance_m=round(dist),
            )
        )
    return sorted(out, key=lambda c: (c.distance_m, c.content_id))

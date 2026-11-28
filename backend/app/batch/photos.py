"""장소 사진 (FR-207, BR-03, DATA-10).

TourAPI 부산 목록(areaBasedList2)의 대표 이미지 주소(firstimage)를 촬영지에 붙인다.
- 공공누리 제1유형(cpyrhtDivCd 'Type1')만 쓴다. 이미지 파일은 받지 않고 주소만 저장한다.
- 잘못 붙은 사진이 사진 없는 것보다 나빠서 맞추기는 보수적으로 한다(match 참고).
- 목록 응답은 맞추는 동안만 쓰고, 저장하는 것은 맞은 사진의 주소·이름뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.core.geo import haversine_m
from app.core.names import norm
from app.core.places import DISTRICT_CENTERS
from app.data import photos as photo_rows
from app.data.tourapi import Place, TourApiClient, TourApiError

log = logging.getLogger(__name__)

# 관광지·문화시설·레포츠·숙박·쇼핑·음식점. 축제(15)·여행코스(25)는 장소가 아니라 뺀다
CONTENT_TYPES = (12, 14, 28, 32, 38, 39)
MAX_DISTANCE_M = 100  # 촬영지 좌표와 TourAPI 좌표가 이 안이어야 같은 장소로 본다
MIN_NAME = 3  # 포함으로 맞출 때 촬영지 이름의 최소 글자 수('서면' 같은 지역명 막기)
# 포함일 때 TourAPI 이름의 나머지가 이것이면 같은 장소로 본다.
# '부산'과 구·군 이름에서 '구'·'군'을 뗀 말.
# 한 글자('중'·'동'·'남' 등)는 다른 말의 일부와 헷갈려서 뺀다
REGION_WORDS = frozenset({"부산", *(r for d in DISTRICT_CENTERS if len(r := norm(d)[:-1]) >= 2)})


@dataclass(frozen=True)
class Spot:
    """좌표가 있는 촬영지."""

    id: int
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Photo:
    location_id: int
    url: str
    title: str
    distance_m: int
    exact: bool  # 이름이 정규화 후 완전히 같음


def name_match(spot: str, title: str) -> str | None:
    """'exact'(정규화한 이름이 같음) · 'contains'(TourAPI 이름이 촬영지 이름을 품음) · None.

    포함은 촬영지 이름(3글자 이상)이 TourAPI 이름 안에 있고, 나머지가 다음 중 하나일 때만 인정한다.
    - 앞에만 붙은 말: TourAPI 이름이 촬영지 이름으로 끝남('동백섬' → '해운대 동백섬')
    - 지역 이름(앞·뒤 어느 쪽이든): '한화리조트' → '한화리조트 해운대', '스카이라인 루지 부산'
    그래서 뒤에 시설 이름이 붙으면('영화의전당' → '영화의전당 라이브러리 자료실') 붙이지 않는다.
    촬영지가 더 길면('새마을금고 청사포지점' ⊃ '청사포') 그 안의 다른 장소라 붙이지 않는다.
    """
    x, y = norm(spot), norm(title)
    if not x or not y:
        return None
    if x == y:
        return "exact"
    if len(x) < MIN_NAME or x not in y:
        return None
    rest = y.replace(x, "", 1)
    return "contains" if y.endswith(x) or rest in REGION_WORDS else None


def https(url: str) -> str:
    """http 주소는 https로(화면의 혼합 콘텐츠 방지). https로도 열림을 2026-11-28 확인."""
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def _rank(p: Photo) -> tuple[bool, int]:
    return (not p.exact, p.distance_m)


def match(spots: Iterable[Spot], places: Iterable[Place]) -> list[Photo]:
    """촬영지마다 제1유형 사진 하나.

    거리 100m 안이고 이름이 맞는 것 중 이름이 같은 것을 먼저, 다음 가까운 것을 고른다.
    """
    usable = [p for p in places if p.image and p.kogl_type1]
    out: list[Photo] = []
    for s in spots:
        best: Photo | None = None
        for p in usable:
            kind = name_match(s.name, p.title)
            if kind is None:
                continue
            d = haversine_m(s.lat, s.lon, p.lat, p.lon)
            if d > MAX_DISTANCE_M:
                continue
            cand = Photo(s.id, https(p.image or ""), p.title, round(d), kind == "exact")
            if best is None or _rank(cand) < _rank(best):
                best = cand
        if best:
            out.append(best)
    return out


async def fetch(client: TourApiClient, types: Sequence[int] = CONTENT_TYPES) -> list[Place]:
    """부산 목록을 종류별로 받는다. 한 종류가 실패하면 그 종류만 빼고 계속한다."""
    places: list[Place] = []
    for t in types:
        try:
            places += await client.busan(t)
        except TourApiError as e:
            log.warning("장소 사진 목록 실패 content_type=%s error=%s", t, e.kind)
    return places


async def refresh(conn, client: TourApiClient) -> dict:
    """목록을 받아 맞춘 사진으로 DATA-10 사진을 바꾼다.

    목록을 하나도 못 받으면(키 오류·장애) 지난 사진을 그대로 둔다.
    """
    places = await fetch(client)
    usable = sum(1 for p in places if p.image and p.kogl_type1)
    if not places:
        return {
            "places": 0,
            "type1_images": 0,
            "matched": 0,
            "skipped": "목록 없음, 지난 사진 유지",
        }
    spots = [Spot(r["id"], r["name"], r["lat"], r["lon"]) for r in await photo_rows.located(conn)]
    found = match(spots, places)
    saved = await photo_rows.replace(conn, [(p.location_id, p.url, p.title) for p in found])
    return {
        "places": len(places),
        "type1_images": usable,
        "matched": saved,
        "exact": sum(1 for p in found if p.exact),
    }

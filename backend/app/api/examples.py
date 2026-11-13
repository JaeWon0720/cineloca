"""F2 API 예시 응답 (임시, #27).

R3이 F2 화면(UI-01·02)을 먼저 붙일 수 있게 정해진 형식의 고정 데이터를 돌려준다.
MapService·LocationRepository 구현(후속 이슈)에서 이 파일과 라우터의 예시 분기를 지운다.
장소명·작품명·좌표는 부산영상위원회 공개 데이터와 도로명주소 API 값이다.
"""

import math
from datetime import date

from app.api.schemas.common import SourceCard
from app.api.schemas.locations import (
    CertInfo,
    LocationDetail,
    LocationHit,
    LocationSearchRequest,
    LocationSearchResponse,
    LocationWork,
    WorkBrief,
)
from app.api.schemas.meta import DistrictOption, MetaResponse, NamedPoint, YearRange
from app.core.genres import GENRE_GROUPS
from app.core.places import (
    BUSAN_STATION,
    DEFAULT_RADIUS_M,
    DISTRICT_CENTERS,
    RADIUS_OPTIONS,
    kakao_directions_url,
)

SOURCES = {
    "DATA-01": SourceCard(
        id="DATA-01",
        provider="부산영상위원회",
        name="영화 속 부산정보",
        base_date=date(2026, 11, 9),
        url="https://www.data.go.kr/data/15164328/openapi.do",
    ),
    "DATA-02": SourceCard(
        id="DATA-02",
        provider="부산영상위원회",
        name="씨네맵",
        base_date=date(2026, 11, 9),
        url="https://www.data.go.kr/data/15164338/openapi.do",
    ),
}

# (id, 장소명, 구·군, 주소, 위도, 경도, 촬영 작품 [(id, 제목, 개봉연도, KMDb 연결)], 장르 묶음)
_PLACES = [
    (
        1,
        "해운대해수욕장",
        "해운대구",
        "부산광역시 해운대구 해운대로570번길 49 (우동)",
        35.160235,
        129.159397,
        [
            (11, "해운대", 2009, True),
            (12, "도마뱀", 2006, True),
            (13, "쓰리 썸머 나잇", 2015, True),
            (14, "그날의 분위기", 2016, True),
        ],
        {"드라마", "코미디", "재난"},
    ),
    (
        2,
        "부산아쿠아리움",
        "해운대구",
        "부산광역시 해운대구 해운대해변로 266 (중동)",
        35.15929,
        129.160583,
        [(15, "도둑들", 2012, True), (16, "소울메이트", 2023, True)],
        {"범죄·느와르", "액션·모험", "드라마"},
    ),
    (
        3,
        "해운대전통시장",
        "해운대구",
        "부산광역시 해운대구 구남로41번길 22-1 (중동)",
        35.161404,
        129.162315,
        [(11, "해운대", 2009, True), (13, "쓰리 썸머 나잇", 2015, True)],
        {"드라마", "코미디", "재난"},
    ),
]


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)))


def search(req: LocationSearchRequest) -> LocationSearchResponse:
    hits = []
    for pid, name, gu, _addr, lat, lon, works, genres in _PLACES:
        d = _distance_m(req.base.lat, req.base.lon, lat, lon)
        years = [w[2] for w in works]
        if d > req.radius_m:
            continue
        if req.gu and gu not in req.gu:
            continue
        if req.genres and not genres & set(req.genres):
            continue
        if req.year_from and max(years) < req.year_from:
            continue
        if req.year_to and min(years) > req.year_to:
            continue
        hits.append(
            LocationHit(
                id=pid,
                name=name,
                gu=gu,
                lat=lat,
                lon=lon,
                distance_m=d,
                works=[WorkBrief(id=w[0], title=w[1], release_year=w[2]) for w in works[:3]],
                work_count=len(works),
                nearest_station=None,
                directions_url=kakao_directions_url(name, lat, lon),
            )
        )
    hits.sort(key=lambda h: h.distance_m)
    return LocationSearchResponse(
        radius_m=req.radius_m, total=len(hits), items=hits, sources=list(SOURCES.values())
    )


def location(location_id: int) -> LocationDetail | None:
    if location_id == 4:  # 좌표 없음 + 주거지 예시(FR-209·210, FR-506)
        return LocationDetail(
            id=4,
            name="학장동 주택가",
            address=None,
            gu="사상구",
            located=False,
            tags=[],
            photos=[],
            caution="residential",
            caution_notice="주민이 사는 곳이에요. 외부에서만 관람해 주세요.",
            cert=CertInfo(eligible=False, radius_m=100, excluded_reason="no_coordinates"),
            works=[
                LocationWork(id=17, title="희생부활자", release_year=2017, kmdb_matched=True),
                LocationWork(id=18, title="퍼펙트 게임", release_year=2011, kmdb_matched=True),
            ],
            directions_url=None,
            sources=[SOURCES["DATA-01"]],
        )
    for pid, name, gu, addr, lat, lon, works, _genres in _PLACES:
        if pid == location_id:
            return LocationDetail(
                id=pid,
                name=name,
                address=addr,
                gu=gu,
                located=True,
                lat=lat,
                lon=lon,
                tags=["바다"] if pid == 1 else [],
                photos=[],
                nearest_station=None,
                cert=CertInfo(eligible=True, radius_m=100),
                works=[
                    LocationWork(id=w[0], title=w[1], release_year=w[2], kmdb_matched=w[3])
                    for w in works
                ],
                directions_url=kakao_directions_url(name, lat, lon),
                sources=list(SOURCES.values()),
            )
    return None


def meta() -> MetaResponse:
    label, lat, lon = BUSAN_STATION
    return MetaResponse(
        default_base=NamedPoint(label=label, lat=lat, lon=lon),
        radius_options=list(RADIUS_OPTIONS),
        default_radius_m=DEFAULT_RADIUS_M,
        districts=[DistrictOption(name=n, lat=a, lon=o) for n, (a, o) in DISTRICT_CENTERS.items()],
        stations=[],
        genres=list(GENRE_GROUPS),
        year_range=YearRange(min=2000, max=2025),
    )

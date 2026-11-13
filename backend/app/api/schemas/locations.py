"""F2 지도 기반 탐색 스키마: API-005 근접·필터 검색, API-006 촬영지 상세 (FR-201~210).

- 기준 위치 좌표는 POST 본문으로만 받고, 응답에 되돌려 주지 않는다(NFR-003, BR-10).
- 좌표가 없는 촬영지는 검색 결과(지도)에 나오지 않는다(FR-210). 상세에서는 located=false.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.common import SourceCard
from app.core.genres import GenreGroup

RadiusM = Literal[500, 1000, 3000]  # FR-203
District = Literal[
    "중구",
    "서구",
    "동구",
    "영도구",
    "부산진구",
    "동래구",
    "남구",
    "북구",
    "해운대구",
    "사하구",
    "금정구",
    "강서구",
    "연제구",
    "수영구",
    "사상구",
    "기장군",
]  # BR-15: 부산광역시 16개 구·군
Caution = Literal["private", "residential", "school"]  # FR-209
CertExcludedReason = Literal["no_coordinates", "private", "residential", "school", "excluded"]


class BasePoint(BaseModel):
    """기준 위치. 현재 위치, 부산역(기본), 고른 구·군·역의 좌표(FR-201·202)."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class LocationSearchRequest(BaseModel):
    """API-005 요청. 필터는 모두 만족하는 촬영지만 고른다(FR-205)."""

    base: BasePoint
    radius_m: RadiusM = Field(default=1000, description="반경(m): 500·1000·3000, 기본 1000")
    genres: list[GenreGroup] = Field(
        default_factory=list, max_length=12, description="장르 묶음(하나라도 맞으면, API-007 목록)"
    )
    year_from: int | None = Field(default=None, ge=1900, le=2100, description="개봉연도 시작(포함)")
    year_to: int | None = Field(default=None, ge=1900, le=2100, description="개봉연도 끝(포함)")
    gu: list[District] = Field(default_factory=list, description="구·군(하나라도 맞으면)")

    @model_validator(mode="after")
    def _check_years(self) -> "LocationSearchRequest":
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from은 year_to보다 클 수 없습니다")
        return self


class StationBrief(BaseModel):
    id: str
    name: str
    line: str = Field(description="호선")
    walk_min: float = Field(ge=0, description="최근접 역까지 도보 시간(분, BR-12)")


class WorkBrief(BaseModel):
    id: int
    title: str
    release_year: int | None = None


class LocationHit(BaseModel):
    """지도 마커·거리순 목록·요약 카드 한 건(FR-203·206·208)."""

    id: int
    name: str
    gu: District | None = None
    lat: float
    lon: float
    distance_m: int = Field(ge=0, description="기준 위치에서 직선거리(m)")
    works: list[WorkBrief] = Field(max_length=3, description="촬영 작품 최대 3편")
    work_count: int = Field(ge=0, description="촬영 작품 전체 수(나머지는 '외 N편')")
    nearest_station: StationBrief | None = None
    directions_url: str = Field(description="카카오맵 길찾기 링크(FR-208)")


class LocationSearchResponse(BaseModel):
    """API-005 응답. items는 거리순. 0건이면 화면에서 반경을 넓히도록 안내한다(UC-03)."""

    radius_m: RadiusM
    total: int = Field(ge=0)
    items: list[LocationHit]
    sources: list[SourceCard]


class Photo(BaseModel):
    """장소 사진. 공공누리 제1유형 이미지 주소만(FR-207, BR-03)."""

    url: str
    title: str | None = None
    source_id: str


class CertInfo(BaseModel):
    """방문 인증 가능 여부(FR-506, BR-06·07)."""

    eligible: bool
    radius_m: int = Field(ge=50, le=300)
    excluded_reason: CertExcludedReason | None = None


class LocationWork(WorkBrief):
    kmdb_matched: bool = Field(
        description="KMDb 연결 여부. false면 '촬영지 정보 제공 작품'(FR-604)"
    )


class LocationDetail(BaseModel):
    """API-006 응답(FR-207~209). 좌표가 없으면 located=false, lat·lon·directions_url은 null."""

    id: int
    name: str
    address: str | None = None
    gu: District | None = None
    located: bool
    lat: float | None = None
    lon: float | None = None
    tags: list[str] = Field(description="승인된 장소 유형 태그만(BR-04)")
    photos: list[Photo]
    nearest_station: StationBrief | None = None
    caution: Caution | None = None
    caution_notice: str | None = Field(default=None, description="'외부에서만 관람' 안내(FR-209)")
    safety_note: str | None = None
    cert: CertInfo
    works: list[LocationWork]
    directions_url: str | None = None
    sources: list[SourceCard]

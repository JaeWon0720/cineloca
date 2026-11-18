"""F1 작품 아카이브 스키마: API-001 목록, API-002 상세, API-003 관람등급 (FR-101~107·802).

- 이름은 F2 스키마(WorkBrief, kmdb_matched, sources)와 R3 화면(#45)의 타입을 따랐다.
- 관람등급은 SDD대로 API-003으로 따로 준다. 값은 KMDb 저장본이다(ADR-007, FR-107).
- 포스터·스틸은 팀 결정으로 보여 주지 않고 KMDb 원문 링크(kmdb_url)로 안내한다(BR-01, #12).
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.common import SourceCard
from app.api.schemas.locations import District, WorkBrief

WorkSort = Literal["title", "year", "locations"]  # 이름순·최신순·촬영지 많은 순(FR-101)


class WorkSummary(WorkBrief):
    """작품 카드 한 건(FR-101)."""

    genres: list[str] = Field(description="KMDb 장르. 목록의 genre 필터에 그대로 쓴다")
    director: str | None = None
    location_count: int = Field(ge=0, description="부산 촬영지 수")
    kmdb_matched: bool = Field(
        description="KMDb 연결 여부. false면 '촬영지 정보 제공 작품'(FR-604)"
    )


class WorkListResponse(BaseModel):
    """API-001 응답. page는 1부터 센다."""

    total: int = Field(ge=0)
    page: int = Field(ge=1)
    size: int = Field(ge=1)
    items: list[WorkSummary]
    sources: list[SourceCard]


class WorkLocation(BaseModel):
    """작품의 부산 촬영지(FR-106). 좌표가 없으면 located=false, lat·lon은 null(FR-210)."""

    id: int
    name: str
    gu: District | None = None
    located: bool
    lat: float | None = None
    lon: float | None = None


class WorkDetail(WorkSummary):
    """API-002 응답. KMDb 미매칭 작품은 작품명과 촬영지만 채워진다(UC-01)."""

    cast: list[str] = Field(max_length=5, description="주요 출연진 최대 5명(FR-103)")
    synopsis: str | None = Field(default=None, description="KMDb 원문 줄거리")
    trailer_url: str | None = Field(default=None, description="KMDb 예고편 링크(FR-105)")
    kmdb_url: str | None = Field(
        default=None, description="KMDb 원문 링크. 포스터·스틸은 여기서 본다(FR-104)"
    )
    locations: list[WorkLocation]
    related: list[WorkBrief] = Field(max_length=5, description="비슷한 작품 최대 5편(FR-802)")
    sources: list[SourceCard]


class WorkRating(BaseModel):
    """API-003 응답. KMDb에 등급이 없으면 rating=null(화면은 '정보 없음'), sources는 빈 목록."""

    rating: str | None = Field(default=None, examples=["15세관람가"])
    sources: list[SourceCard]

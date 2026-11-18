"""F1 작품 아카이브 스키마: API-001 목록, API-002 상세 (FR-101~106·802).

- 이름은 F2 스키마(WorkBrief, kmdb_matched, sources)와 R3 화면(#45)의 타입을 따랐다.
- 관람등급은 상세에 넣지 않고 API-003(다음 PR)으로 따로 준다(KOBIS가 느려도 상세 화면을 막지 않게).
- 영화 이미지는 주소만 전달하고 파일은 저장하지 않는다(BR-01).
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
    poster_url: str | None = Field(default=None, description="KMDb 포스터 주소(FR-104)")
    still_urls: list[str] = Field(default_factory=list, description="KMDb 스틸 주소(FR-104)")
    trailer_url: str | None = Field(default=None, description="KMDb 예고편 링크(FR-105)")
    kmdb_url: str | None = Field(default=None, description="KMDb 작품 페이지")
    locations: list[WorkLocation]
    related: list[WorkBrief] = Field(max_length=5, description="비슷한 작품 최대 5편(FR-802)")
    sources: list[SourceCard]

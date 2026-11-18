"""API-012 코스 주변 시설 추천 스키마 (FR-414~418, FR-707).

조건 문장은 마스킹한 뒤에만 AI로 보내고 원문은 저장하지 않는다(FR-702). 사용자 위치는 받지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.common import SourceCard
from app.core.facilities import FacilityKind, FoodCategory, LodgingCategory


class SelectedConditions(BaseModel):
    """직접 고른 분류(FR-418 직접 고르기). 문장과 다르면 이 값이 우선이다."""

    kinds: list[FacilityKind] = Field(default_factory=list, max_length=2)
    food: list[FoodCategory] = Field(default_factory=list, max_length=2)
    lodging: list[LodgingCategory] = Field(default_factory=list, max_length=6)


class AmenityRequest(BaseModel):
    location_ids: list[int] = Field(
        min_length=2, max_length=10, description="코스의 촬영지 ID, 방문 순서대로(FR-403)"
    )
    text: str | None = Field(
        default=None, max_length=200, description="원하는 조건(선택). 예: '카페랑 밥집'"
    )
    selected: SelectedConditions | None = None


class ConditionsOut(BaseModel):
    """해석한 조건. status: ai(문장 정리) · selected(고른 값만) · manual(AI 실패, 직접 고르기)."""

    status: Literal["ai", "selected", "manual"]
    kinds: list[FacilityKind]
    food: list[FoodCategory]
    lodging: list[LodgingCategory]
    keywords: list[str]
    masked: bool = Field(description="조건 문장에서 개인정보를 가렸는지")


class AmenityPick(BaseModel):
    content_id: str = Field(description="TourAPI 콘텐츠 ID")
    name: str
    category: str | None = Field(default=None, description="밥·카페 / 숙박 6종. 모르면 null")
    address: str | None = None
    lat: float
    lon: float
    distance_m: int = Field(ge=0, description="구간 중간 지점·마지막 촬영지에서 직선거리")
    matched: list[str] = Field(description="맞은 조건(예: '분류: 카페')")
    reason: str = Field(description="추천 이유 한 문장")
    ai_generated: bool = Field(description="true면 'AI 생성' 표시(FR-707), false면 템플릿 문장")
    directions_url: str


class AmenitySegment(BaseModel):
    """구간(food: from_order~to_order 촬영지 사이) 또는 마지막 촬영지 근처 숙박(lodging)."""

    key: str
    kind: FacilityKind
    from_order: int = Field(ge=1)
    to_order: int | None = None
    anchor: str = Field(description="기준 설명. 예: '자갈치시장~범일동 철길 사이'")
    notice: str | None = Field(
        default=None, description="'근처 1km 안에 시설이 없어요'·'시설 정보를 불러오지 못했어요' 등"
    )
    picks: list[AmenityPick] = Field(max_length=3)


class AmenityResponse(BaseModel):
    conditions: ConditionsOut
    notices: list[str]
    segments: list[AmenitySegment]
    sources: list[SourceCard]

"""관리자 검수 큐·촬영지 속성 스키마: API-022 (FR-608·609, BR-04), API-023 (FR-610, BR-06·07).

화면 타입 frontend/src/api/admin.ts(ReviewItem·ReviewListResponse·AdminLocationAttrs)와 같다.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.api.schemas.locations import Caution

ReviewKind = Literal["tag", "match"]
ReviewStatus = Literal["pending", "approved", "modified", "rejected"]


class ReviewItem(BaseModel):
    id: int
    kind: ReviewKind
    target_type: Literal["location", "work"]
    target_id: int
    target_name: str = Field(description="작품 제목 또는 촬영지 이름(대상이 없어졌으면 '(삭제됨)')")
    target_year: int | None = Field(description="작품이면 개봉연도, 촬영지면 null")
    proposer: Literal["rule", "ai"]
    score: float | None
    status: ReviewStatus
    proposal: dict[str, Any] = Field(
        description="태그 {tags, reason, error?} · 매칭 {kmdb_id, kmdb_title, …}·AI {verdict, …}"
    )
    created_at: datetime
    handled_at: datetime | None


class ReviewCounts(BaseModel):
    pending: int
    handled: int


class ReviewListResponse(BaseModel):
    total: int = Field(ge=0, description="조건에 맞는 전체 수")
    page: int = Field(ge=1)
    size: int = Field(ge=1)
    counts: ReviewCounts = Field(description="종류와 상관없는 대기·처리 수(탭 숫자)")
    items: list[ReviewItem]


class ReviewAction(BaseModel):
    """modify면 태그 항목은 tags, 매칭 항목은 kmdb_id에 고친 값을 넣는다."""

    action: Literal["approve", "modify", "reject"]
    tags: list[str] | None = Field(default=None, max_length=20)
    kmdb_id: str | None = Field(default=None, max_length=10, examples=["K24589"])


class AdminLocationAttrs(BaseModel):
    id: int
    name: str
    gu: str | None
    address: str | None
    located: bool = Field(description="좌표가 있는지")
    tags: list[str]
    caution: Caution | None
    safety_note: str | None
    cert_radius_m: int = Field(ge=50, le=300)
    cert_excluded: bool


class AdminLocationPatch(BaseModel):
    """보낸 항목만 고친다. caution·safety_note는 null로 지울 수 있다."""

    tags: list[str] | None = Field(default=None, max_length=20)
    caution: Caution | None = None
    safety_note: str | None = Field(default=None, max_length=1000)
    cert_radius_m: int | None = Field(default=None, description="50~300(BR-07)")
    cert_excluded: bool | None = Field(default=None, description="방문 인증 제외(BR-06)")

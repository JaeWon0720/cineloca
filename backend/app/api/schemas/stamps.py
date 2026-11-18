"""F5 방문 인증 스키마: API-016 인증, API-017 토큰 확인, API-018 완주 배지 (FR-501~508).

위치(lat·lon·accuracy_m)는 POST 본문으로만 받고 응답·로그·토큰에 넣지 않는다(NFR-003, BR-10).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.locations import WorkBrief

Token = str
MAX_TOKENS = 2000  # 촬영지 전체(약 1,400곳) 스탬프 + 배지를 담을 만큼
MAX_COURSE_STOPS = 30


class StampRequest(BaseModel):
    """API-016 요청. held: 브라우저에 있는 스탬프 토큰(이미 인증 확인용, BR-08)."""

    location_id: int = Field(ge=1)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0, le=100_000, description="브라우저가 알려 준 위치 정확도(m)")
    held: list[Token] = Field(default_factory=list, max_length=MAX_TOKENS)


class StampResponse(BaseModel):
    """ok=false면 reason: low_accuracy(실외에서 다시)·out_of_range(거리 안내)·already_certified."""

    ok: bool
    token: str | None = Field(default=None, description="스탬프 토큰. 브라우저에만 저장(FR-504)")
    reason: Literal["low_accuracy", "out_of_range", "already_certified"] | None = None
    distance_m: int | None = Field(default=None, ge=0, description="촬영지까지 거리(m)")


class VerifyRequest(BaseModel):
    tokens: list[Token] = Field(max_length=MAX_TOKENS)


class StampLocation(BaseModel):
    id: int
    name: str


class StampEntry(BaseModel):
    """스탬프북 한 칸(FR-507): 촬영지명·작품(최근 3편)·인증 일시."""

    location: StampLocation
    works: list[WorkBrief] = Field(max_length=3)
    work_count: int = Field(ge=0)
    certified_at: datetime


class BadgeEntry(BaseModel):
    course_key: str
    course_title: str | None = Field(default=None, description="추천 코스 이름. 저장 코스는 null")
    issued_at: datetime


class VerifyResponse(BaseModel):
    """유효한 토큰만 보여 준다. 위조·변조 토큰은 개수만(ERR-010)."""

    valid: list[StampEntry]
    badges: list[BadgeEntry]
    invalid_count: int = Field(ge=0)
    unavailable_count: int = Field(
        ge=0, description="서명은 맞지만 촬영지가 데이터에서 빠진 스탬프"
    )


class CourseRef(BaseModel):
    """추천 코스는 id, 브라우저 저장 코스는 location_ids(방문 순서) 중 하나만."""

    id: int | None = Field(default=None, ge=1)
    location_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_COURSE_STOPS)

    @model_validator(mode="after")
    def _one_of(self) -> "CourseRef":
        if (self.id is None) == (self.location_ids is None):
            raise ValueError("id와 location_ids 중 하나만 보내 주세요")
        return self


class BadgeRequest(BaseModel):
    course: CourseRef
    stamps: list[Token] = Field(max_length=MAX_TOKENS)


class BadgeResponse(BaseModel):
    """ok=false면 reason: missing_stamps(missing = 아직 인증 안 한 장소 ID) · too_few_targets."""

    ok: bool
    token: str | None = None
    reason: Literal["missing_stamps", "too_few_targets"] | None = None
    course_key: str
    missing: list[int] = Field(default_factory=list)

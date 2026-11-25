"""추천 코스 관리 스키마: API-024 후보 생성·수정·승인 (FR-614, UI-12)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.schemas.courses import CourseDetail, Theme

CourseStatus = Literal["draft", "approved"]


class AdminCourseSummary(BaseModel):
    id: int
    title: str
    theme: Theme
    theme_ref: str | None
    status: CourseStatus
    approved_at: datetime | None
    start_station_id: str | None
    stop_count: int = Field(ge=0)
    total_min: int | None = Field(description="계산할 수 없으면 null(problem 참고)")
    total_distance_m: int | None
    problem: str | None = Field(description="승인 전에 고쳐야 할 점")


class AdminCourseList(BaseModel):
    items: list[AdminCourseSummary]


class AdminCourseDetail(CourseDetail):
    """사용자 화면(API-010)과 같은 계산에 상태를 더한 것."""

    status: CourseStatus
    approved_at: datetime | None
    start_station_id: str | None = Field(description="출발역(없으면 첫 장소에서 출발)")


class CandidateRequest(BaseModel):
    theme: Theme
    theme_ref: str = Field(
        min_length=1, max_length=20, description="작품 ID·인물 ID 또는 구·군 이름"
    )
    max_stops: int = Field(default=5, ge=2, le=10, description="모을 촬영지 수(2~10)")


class StopEditIn(BaseModel):
    location_id: int = Field(ge=1)
    stay_min: int = Field(default=20, ge=0, le=240, description="체류 시간(기본 20분, BR-13)")


class CoursePatch(BaseModel):
    """보낸 항목만 고친다. 제목·장소·출발역을 고치면 승인 전으로 돌아가고,
    status=approved를 함께 보내면 고친 뒤 바로 승인한다."""

    title: str | None = Field(default=None, max_length=200)
    stops: list[StopEditIn] | None = Field(default=None, max_length=10, description="방문 순서대로")
    start_station_id: str | None = Field(default=None, max_length=10, description="null이면 없앰")
    status: CourseStatus | None = None

"""F4 코스 스키마: API-011 나만의 코스 경로 (FR-403~405·413, BR-12·13).

현재 위치 좌표는 요청 본문으로만 받고 응답·로그에 남기지 않는다(BR-10).
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.locations import CertInfo, District


class StartIn(BaseModel):
    """출발지: 현재 위치(lat·lon) · 도시철도역(id = 역 코드) · 촬영지(id = 촬영지 ID)."""

    type: Literal["current", "station", "location"]
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    id: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _fields(self) -> "StartIn":
        if self.type == "current":
            if self.lat is None or self.lon is None or self.id is not None:
                raise ValueError("현재 위치는 lat·lon만 보내 주세요")
        elif self.id is None or self.lat is not None or self.lon is not None:
            raise ValueError("역·촬영지 출발은 id만 보내 주세요")
        elif self.type == "location" and not self.id.isdigit():
            raise ValueError("촬영지 id는 숫자입니다")
        return self


class PlanRequest(BaseModel):
    start: StartIn
    location_ids: list[int] = Field(
        min_length=1, max_length=10, description="촬영지 ID. 출발 촬영지를 포함해 2~10곳"
    )
    fixed_order: bool = Field(default=False, description="true면 순서를 바꾸지 않음(FR-405)")


class StartOut(BaseModel):
    type: Literal["current", "station", "location"]
    label: str
    lat: float | None = Field(default=None, description="현재 위치면 null(BR-10)")
    lon: float | None = None


class CourseStop(BaseModel):
    order: int = Field(ge=1)
    id: int
    name: str
    gu: District | None = None
    lat: float
    lon: float
    stay_min: int = Field(description="체류 시간(BR-13)")
    cert: CertInfo


class StationRef(BaseModel):
    id: str
    name: str
    line: str


class CourseLeg(BaseModel):
    """구간. from_order 0은 출발지(현재 위치·역)."""

    from_order: int = Field(ge=0)
    to_order: int = Field(ge=1)
    mode: Literal["walk", "subway"]
    minutes: int = Field(ge=0)
    distance_m: int = Field(ge=0, description="두 지점 직선거리")
    board: StationRef | None = None
    alight: StationRef | None = None
    walk_to_board_min: int = 0
    ride_min: int = 0
    walk_from_alight_min: int = 0


class CoursePlan(BaseModel):
    start: StartOut
    stops: list[CourseStop]
    legs: list[CourseLeg]
    travel_min: int
    stay_min: int
    total_min: int
    total_distance_m: int
    optimized: bool = Field(description="false면 받은 순서 그대로(FR-405)")
    subway_data: bool = Field(
        description="역간 소요시간이 있으면 true. 없으면 도보로만 계산(UC-15)"
    )

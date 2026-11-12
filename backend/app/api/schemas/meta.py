"""API-007 선택 목록 스키마 (FR-202 위치 직접 선택, FR-205 지도 필터, FR-310 필터 패널)."""

from pydantic import BaseModel, Field

from app.api.schemas.locations import District, RadiusM


class NamedPoint(BaseModel):
    label: str
    lat: float
    lon: float


class DistrictOption(BaseModel):
    name: District
    lat: float = Field(description="구·군 중심점(구청·군청 좌표)")
    lon: float


class StationOption(BaseModel):
    id: str
    name: str
    line: str
    lat: float
    lon: float


class YearRange(BaseModel):
    min: int
    max: int


class MetaResponse(BaseModel):
    default_base: NamedPoint = Field(description="위치를 못 얻을 때 기준 위치(부산역, FR-201)")
    radius_options: list[RadiusM]
    default_radius_m: RadiusM
    districts: list[DistrictOption]
    stations: list[StationOption] = Field(description="도시철도역(C1 적재 전에는 빈 목록)")
    genres: list[str]
    year_range: YearRange | None = Field(default=None, description="작품 개봉연도 범위")

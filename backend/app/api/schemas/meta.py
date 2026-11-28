"""API-007 선택 목록 스키마 (FR-202 위치 직접 선택, FR-205 지도 필터, FR-310 필터 패널)."""

from pydantic import BaseModel, Field

from app.api.schemas.locations import District, RadiusM
from app.core.genres import GenreGroup


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
    genres: list[GenreGroup] = Field(description="장르 묶음(KMDb 장르를 12개로 묶음)")
    year_range: YearRange | None = Field(default=None, description="작품 개봉연도 범위")


class PersonOption(BaseModel):
    """API-026 인물 후보(FR-310 필터 패널). 고른 값은 필터의 persons에 {id, name}으로 넣는다."""

    id: int
    name: str
    work: str | None = Field(default=None, description="동명이인 구분용 대표 작품(가장 최근 작품)")


class PersonList(BaseModel):
    items: list[PersonOption]

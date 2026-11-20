"""F3 AI 대화형 탐색 스키마: API-013 질의·고르기·칩 지우기, API-014 필터 패널 검색 (FR-301~312).

- 응답은 QueryService의 QueryResult 필드를 그대로 옮긴 것이다.
- 기준 위치 좌표('내 위치')는 POST 본문의 base로만 받고, 응답·세션·로그에 남기지 않는다(BR-10).
- 세션 ID는 X-Session-Id 요청 헤더로 받는다(SDD 6장). 서버가 만든 ID를 응답 session_id로 돌려주므로
  다음 요청부터 그 값을 헤더에 넣는다.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.api.schemas.common import SourceCard
from app.api.schemas.locations import BasePoint, District
from app.core.genres import GenreGroup

QueryStatus = Literal["ok", "not_found", "choose", "out_of_scope", "ai_unavailable"]
ChoiceKey = Literal["works", "persons", "anchor"]
ChipKey = Literal["works", "persons", "genres", "districts", "years", "anchor", "travel"]


class RefModel(BaseModel):
    """사전 표준 값(작품·인물·역·촬영지)."""

    id: str = Field(min_length=1, max_length=20, description="작품·인물·촬영지 번호, 역 코드")
    name: str = Field(max_length=100)


class AnchorModel(BaseModel):
    type: Literal["station", "place", "current"]
    ref: RefModel | None = Field(default=None, description="station에서 비우면 '아무 역'")


class TravelModel(BaseModel):
    mode: Literal["walk", "subway"]
    max_min: int | None = Field(default=None, ge=1, le=300)


class FiltersModel(BaseModel):
    """확정된 검색 조건. 필터 패널(FR-310)이 보내고, AI 실패 때 미리 채울 값으로 받는다(FR-311)."""

    works: list[RefModel] = Field(default_factory=list, max_length=10)
    persons: list[RefModel] = Field(default_factory=list, max_length=10)
    genres: list[GenreGroup] = Field(default_factory=list, max_length=12)
    districts: list[District] = Field(default_factory=list, max_length=16)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    anchor: AnchorModel | None = None
    travel: TravelModel | None = None
    result_type: Literal["location", "work"] = "location"

    @model_validator(mode="after")
    def _check_years(self) -> "FiltersModel":
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from은 year_to보다 클 수 없습니다")
        return self


class QueryRequest(BaseModel):
    """API-013 요청. 글자 수(200자)는 서비스가 확인해 400으로 알린다(FR-301)."""

    text: str = Field(max_length=2000, description="질문(200자까지)")
    base: BasePoint | None = Field(default=None, description="현재 위치('내 위치' 조건에만 씀)")


class ChooseRequest(BaseModel):
    """후보가 여럿일 때 하나 고르기(FR-303)."""

    key: ChoiceKey
    option_id: str = Field(max_length=40)
    base: BasePoint | None = None


class RemoveChipRequest(BaseModel):
    """해석 조건 칩 하나 지우고 다시 검색(FR-312)."""

    key: ChipKey
    base: BasePoint | None = None


class SearchRequest(BaseModel):
    """API-014 요청."""

    conditions: FiltersModel
    base: BasePoint | None = None


class Chip(BaseModel):
    key: ChipKey
    label: str


class Option(BaseModel):
    id: str
    label: str = Field(examples=["친구 (2001)"])


class Choice(BaseModel):
    key: ChoiceKey
    name: str = Field(description="사용자가 말한 이름")
    options: list[Option]


class WorkBrief(BaseModel):
    title: str
    year: int | None = None


class Hit(BaseModel):
    location_id: int
    name: str
    gu: str | None = None
    lat: float
    lon: float
    works: list[WorkBrief] = Field(description="조건에 맞는 작품 먼저")
    station: str | None = Field(default=None, description="가장 가까운 역")
    walk_min: int | None = Field(default=None, description="그 역에서 도보 분")
    source_ids: list[str] = Field(description="이 촬영지의 출처 ID(응답 sources의 id, FR-704)")


class SearchResponse(BaseModel):
    """API-014 응답."""

    status: Literal["ok", "not_found"]
    notices: list[str]
    chips: list[Chip]
    relaxed: list[str] = Field(description="결과가 없어 푼 조건(FR-305)")
    total: int = Field(description="조건에 맞는 전체 수(hits는 20건까지)")
    hits: list[Hit] = Field(max_length=20)
    filter_panel: FiltersModel | None = None
    sources: list[SourceCard] = Field(
        description="hits에 쓰인 출처 카드(제공기관·데이터명·링크, FR-704)"
    )


class QueryResponse(SearchResponse):
    """API-013 응답. status가 choose면 choices에서 고르고, ai_unavailable·not_found면
    filter_panel 값으로 필터 패널을 연다(FR-311)."""

    status: QueryStatus  # type: ignore[assignment]
    session_id: str
    masked: bool = Field(description="개인정보로 보이는 내용을 가렸는지(FR-702)")
    unknown: list[str] = Field(description="사전에 없어 뺀 이름(FR-303)")
    summary: list[str] = Field(description="결과 요약 문장(최대 3개, FR-307)")
    ai_generated: bool = Field(description="summary가 AI 생성 문장인지(FR-707)")
    choices: list[Choice]

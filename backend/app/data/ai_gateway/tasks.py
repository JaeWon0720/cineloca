"""작업별 출력 스키마와 설정 (SDD 2.3 작업별 모델·입출력 표, AI-REQ-04·14).

출력 모델은 Pydantic으로 검증한다. 허용 값 밖(예: 목록에 없는 장르·구)도 스키마 위반으로 본다.
extract_query는 착수 PoC p5(프롬프트 v2, 필드 일치율 qwen3:8b 93.4%·Gemini 99.5%)의
스키마를 바탕으로, 장르만 검색 필터의 장르 묶음 12개로 바꿨다(프롬프트 v3).
묶음에 없는 장르를 다른 묶음에 넣지 않게 규칙을 더했다(프롬프트 v4, #35 재평가는 PoC 보고서).
C1 제안(suggest_tags·judge_match)은 FR-608에서 확정했다(프롬프트 v1).
structure_facility·facility_reasons(F4 코스 주변 시설, FR-415·417, CR-01)는 v1이다.
CR-01(11/9 수정본)로 AI 맞춤 코스(취향 구조화)와 코스 소개 생성이 빠져
course_intro는 등록하지 않는다.
기능 번호는 수정본 기준: F3 = AI 대화형 탐색(FR-301~312), F4 = 코스 + 주변 시설 추천.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.facilities import FacilityKind, FoodCategory, LodgingCategory
from app.core.genres import GENRE_GROUPS, GenreGroup
from app.core.place_tags import PlaceType

# 장르 허용 값 = 검색 필터의 장르 묶음(app/core/genres.py).
# AI가 뽑은 값이 API-005 요청에서 그대로 통과해야 한다.
GENRES: tuple[str, ...] = tuple(GENRE_GROUPS)

DISTRICTS = (
    "중구",
    "서구",
    "동구",
    "영도구",
    "부산진구",
    "동래구",
    "남구",
    "북구",
    "해운대구",
    "사하구",
    "금정구",
    "강서구",
    "연제구",
    "수영구",
    "사상구",
    "기장군",
)

District = Literal[DISTRICTS]  # type: ignore[valid-type]


# ── extract_query (F3 조건 추출, FR-302) ─────────────────────
class Anchor(BaseModel):
    type: Literal["station", "place", "current"]
    name: str | None = None


class Travel(BaseModel):
    mode: Literal["walk", "subway"]
    max_min: int | None = Field(default=None, ge=1, le=300)


class QueryConditions(BaseModel):
    out_of_scope: bool
    work_titles: list[str]
    persons: list[str]
    genres: list[GenreGroup]  # 검색 API(API-005)와 같은 장르 묶음 12개
    districts: list[District]
    release_year_from: int | None = Field(ge=1900, le=2100)
    release_year_to: int | None = Field(ge=1900, le=2100)
    anchor: Anchor | None
    travel: Travel | None
    result_type: Literal["location", "work"]


# ── 근거 기반 문장 생성 (F3 요약, F1 소개). 출력은 COMP-09가 문장마다 대조한다 ──
class GroundedSentence(BaseModel):
    text: str = Field(min_length=1)
    entities: list[str]  # 문장에 나온 인명·작품명·장소명(COMP-09 대조용)


class GroundedText(BaseModel):
    sentences: list[GroundedSentence] = Field(min_length=1)


# ── F4 코스 주변 시설 (FR-415·417, CR-01) ───────────────────
class FacilityConditions(BaseModel):
    """원하는 조건(FR-415). 말하지 않은 항목은 비운다.

    kinds가 비면 음식·숙박 모두, 분류가 비면 그 종류의 모든 분류를 뜻한다.
    keywords는 시설 이름에 들어 있으면 좋은 말(예: '국밥', '오션뷰')로 점수에만 쓴다.
    """

    kinds: list[FacilityKind] = Field(default_factory=list)
    food: list[FoodCategory] = Field(default_factory=list)
    lodging: list[LodgingCategory] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list, max_length=5)


class FacilityReason(BaseModel):
    number: int = Field(ge=1)  # 입력 목록의 번호
    text: str = Field(min_length=1, max_length=150)
    entities: list[str]  # 문장에 쓴 시설명·장소명(COMP-09 대조용)


class FacilityReasons(BaseModel):
    """추천 이유(FR-417). 시설마다 한 문장. 대조에서 지워지면 템플릿 문장으로 바꾼다."""

    reasons: list[FacilityReason]


# ── C1 배치 제안(검수 큐로만 감, FR-608·BR-04) ──────────────
# 입력은 공공데이터·KMDb 값이라 마스킹 대상(user_text_fields)이 없다.
@dataclass(frozen=True)
class TagInput:
    """suggest_tags 입력: 규칙(FR-607)으로 유형을 정하지 못한 촬영지."""

    location_id: int
    name: str
    address: str | None = None
    gu: str | None = None


class TagSuggestion(BaseModel):
    # 이름·주소로 알 수 없으면 빈 목록(추측 금지). 많아야 2개.
    tags: list[PlaceType] = Field(max_length=2)
    reason: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class MatchCandidate:
    """KMDb 후보 하나. busan_evidence: KMDb 촬영 장소·줄거리에 부산 또는 이 작품 촬영지가 있음."""

    docid: str
    title: str
    year: int | None = None
    kind: str | None = None  # KMDb 유형(극영화, TV-드라마 등)
    director: str | None = None
    busan_evidence: bool = False


@dataclass(frozen=True)
class MatchInput:
    """judge_match 입력: 매칭 점수 0.6~0.9로 검수 큐에 간 작품(BR-11)."""

    work_id: int
    title: str  # 촬영지 데이터의 작품명
    year: int | None  # 촬영지 데이터의 개봉·방영 연도
    candidates: tuple[MatchCandidate, ...]
    places: tuple[str, ...] = ()  # 이 작품의 촬영지 이름 몇 곳(판정 근거)


class MatchJudgement(BaseModel):
    # match: 후보 중 같은 작품(candidate 번호, 1부터) / not_listed: KMDb에 없는 작품으로 보임
    # unsure: 근거 부족. 최종 결정은 사람이 한다(BR-04).
    verdict: Literal["match", "not_listed", "unsure"]
    candidate: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _candidate_only_for_match(self) -> MatchJudgement:
        if (self.verdict == "match") != (self.candidate is not None):
            raise ValueError("candidate는 verdict가 match일 때만, 그때는 반드시 넣는다")
        return self


@dataclass(frozen=True)
class TaskSpec:
    name: str
    output: type[BaseModel]
    providers: tuple[str, ...] = ("ollama", "gemini")  # 대체 체인 순서(ADR-004)
    temperature: float = 0.0  # 0~0.3 (SDD 2.3)
    timeout_s: float = 10.0  # 공급자 1회 제한 시간(NFR-002)
    prompt_version: str = "v0"
    user_text_fields: tuple[str, ...] = ()  # 마스킹(MaskedInput)이 필수인 입력 이름
    cache_ttl_s: int | None = None  # 응답 캐시 시간(AI-REQ-15). None이면 캐시하지 않음(배치 작업)


_DAY_S = 24 * 60 * 60  # 사용자 화면 작업은 24시간 캐시(AI-REQ-15)

TASKS: dict[str, TaskSpec] = {
    spec.name: spec
    for spec in (
        TaskSpec(
            "extract_query",
            QueryConditions,
            prompt_version="v5",  # v4 묶음 밖 장르 뺌 → v5 범위·구·이동·인물 규칙(11/25 N-04)
            user_text_fields=("query",),
            cache_ttl_s=_DAY_S,
        ),
        TaskSpec(
            "summarize_results",  # F3 결과 요약(FR-307)
            GroundedText,
            temperature=0.2,
            prompt_version="v1",
            user_text_fields=("query",),
            cache_ttl_s=_DAY_S,
        ),
        TaskSpec(
            "work_intro",  # F1 작품 소개(FR-109)
            GroundedText,
            temperature=0.0,  # 판마다 오류 수가 달라지던 편차를 줄임(N-05)
            prompt_version="v5",
            cache_ttl_s=_DAY_S,
        ),
        TaskSpec(
            "structure_facility",  # F4 주변 시설 조건(FR-415)
            FacilityConditions,
            prompt_version="v1",
            user_text_fields=("text",),
            cache_ttl_s=_DAY_S,
        ),
        TaskSpec(
            "facility_reasons",  # F4 시설 추천 이유(FR-417)
            FacilityReasons,
            temperature=0.2,
            prompt_version="v1",
            cache_ttl_s=_DAY_S,
        ),
        TaskSpec("suggest_tags", TagSuggestion, prompt_version="v1"),  # FR-608
        TaskSpec("judge_match", MatchJudgement, prompt_version="v1"),  # FR-608
    )
}

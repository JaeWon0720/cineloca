"""작업별 출력 스키마와 설정 (SDD 2.3 작업별 모델·입출력 표, AI-REQ-04·14).

출력 모델은 Pydantic으로 검증한다. 허용 값 밖(예: 목록에 없는 장르·구)도 스키마 위반으로 본다.
extract_query는 착수 PoC p5(프롬프트 v2, 필드 일치율 qwen3:8b 93.4%·Gemini 99.5%)의
스키마를 바탕으로, 장르만 검색 필터의 장르 묶음 12개로 바꿨다(프롬프트 v3).
C1 제안(suggest_tags·judge_match)은 FR-608에서 확정했다(프롬프트 v1).
나머지 작업의 스키마는 SDD 표를 옮긴 초안이며 스프린트 2에서 담당 기능과 함께 확정한다.
structure_preference(F3 취향 구조화)는 Preference 형식이 아직 정해지지 않아 등록하지 않았다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


# ── extract_query (F4 조건 추출, FR-402) ─────────────────────
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


# ── 근거 기반 문장 생성 (F4 요약, F1 소개) — 초안 ─────────────
class GroundedSentence(BaseModel):
    text: str = Field(min_length=1)
    entities: list[str]  # 문장에 나온 인명·작품명·장소명(COMP-09 대조용)


class GroundedText(BaseModel):
    sentences: list[GroundedSentence] = Field(min_length=1)


# ── F3 코스 소개 — 초안 ──────────────────────────────────────
class CourseIntro(BaseModel):
    reasons: list[str]
    intro: str = Field(min_length=1)


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


TASKS: dict[str, TaskSpec] = {
    spec.name: spec
    for spec in (
        TaskSpec(
            "extract_query",
            QueryConditions,
            prompt_version="v3",  # v2(PoC) → v3: 장르 묶음 12개
            user_text_fields=("query",),
        ),
        TaskSpec("summarize_results", GroundedText, temperature=0.2),
        TaskSpec("work_intro", GroundedText, temperature=0.3),
        TaskSpec("course_intro", CourseIntro, temperature=0.3),
        TaskSpec("suggest_tags", TagSuggestion, prompt_version="v1"),  # FR-608
        TaskSpec("judge_match", MatchJudgement, prompt_version="v1"),  # FR-608
    )
}

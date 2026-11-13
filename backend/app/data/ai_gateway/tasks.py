"""작업별 출력 스키마와 설정 (SDD 2.3 작업별 모델·입출력 표, AI-REQ-04·14).

출력 모델은 Pydantic으로 검증한다. 허용 값 밖(예: 목록에 없는 장르·구)도 스키마 위반으로 본다.
extract_query는 착수 PoC p5(프롬프트 v2, 필드 일치율 qwen3:8b 93.4%·Gemini 99.5%)의
스키마를 그대로 쓴다.
나머지 작업의 스키마는 SDD 표를 옮긴 초안이며 스프린트 2에서 담당 기능과 함께 확정한다.
structure_preference(F3 취향 구조화)는 Preference 형식이 아직 정해지지 않아 등록하지 않았다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

GENRES = (
    "드라마",
    "액션",
    "범죄",
    "스릴러",
    "코미디",
    "멜로/로맨스",
    "공포(호러)",
    "SF",
    "판타지",
    "미스터리",
    "전쟁",
    "가족",
    "애니메이션",
    "다큐멘터리",
    "사극",
    "어드벤처",
)
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

Genre = Literal[GENRES]  # type: ignore[valid-type]
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
    genres: list[Genre]
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


# ── C1 배치 제안(검수 큐로만 감, FR-608) — 초안 ───────────────
class TagSuggestion(BaseModel):
    tags: list[str]
    reason: str


class MatchJudgement(BaseModel):
    verdict: str  # 값 목록은 C1 매칭 규칙(BR-11)과 함께 확정
    reason: str


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
            prompt_version="v2",
            user_text_fields=("query",),
        ),
        TaskSpec("summarize_results", GroundedText, temperature=0.2),
        TaskSpec("work_intro", GroundedText, temperature=0.3),
        TaskSpec("course_intro", CourseIntro, temperature=0.3),
        TaskSpec("suggest_tags", TagSuggestion),
        TaskSpec("judge_match", MatchJudgement),
    )
}

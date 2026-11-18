"""F1 AI 작품 소개 스키마: API-004 (FR-108~111, FR-707).

- 요청: 스포일러 단계(none 기본 / partial). '없음'은 줄거리·장면 설명을 근거에 넣지 않는다.
- 응답 status: ok(AI 소개) / degraded(원문 줄거리·촬영지로 강등, FR-110) /
  not_eligible(KMDb 미연결 작품, FR-108). 화면은 ai_generated가 true일 때 'AI 생성'을 표시한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

SpoilerLevel = Literal["none", "partial"]


class WorkIntroRequest(BaseModel):
    spoiler: SpoilerLevel = Field(
        default="none", description="스포일러 단계. none: 줄거리 없이, partial: 결말 빼고 줄거리"
    )


class IntroLocation(BaseModel):
    name: str
    gu: str | None = None


class IntroFallback(BaseModel):
    """강등 화면(FR-110): KMDb 원문 줄거리와 부산 촬영지. 줄거리는 화면이 접어 둔다."""

    synopsis: str | None = None
    locations: list[IntroLocation]


class WorkIntroResponse(BaseModel):
    """API-004 응답."""

    status: Literal["ok", "degraded", "not_eligible"]
    spoiler: SpoilerLevel
    sentences: list[str] = Field(description="근거 대조를 통과한 소개 문장(ok일 때 2개 이상)")
    ai_generated: bool = Field(description="AI가 만든 문장이면 true(FR-707 'AI 생성' 표시)")
    fallback: IntroFallback | None = Field(default=None, description="degraded일 때 보여 줄 원문")
    notice: str | None = Field(default=None, description="화면 안내 문구")
    reason: str | None = Field(
        default=None,
        description="강등 이유: AI_UNAVAILABLE·AI_SCHEMA·AI_DEADLINE·NO_EVIDENCE·TOO_FEW",
    )
    dropped_count: int = Field(ge=0, description="근거 대조에서 지운 문장 수")

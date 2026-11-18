"""F1 AI 작품 소개 API: API-004 (FR-108~111, AI-REQ-01·06·07, SD-01).

작품 조회(PgWorkFacts) → WorkIntroService(근거 묶음 → AI → 근거 대조 → 강등)를 잇는다.
AI가 실패하거나 남은 문장이 2개 미만이어도 200으로 원문 줄거리·촬영지를 돌려준다(FR-110).
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_notice
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.common import ErrorResponse
from app.api.schemas.work_intro import (
    IntroFallback,
    IntroLocation,
    WorkIntroRequest,
    WorkIntroResponse,
)
from app.services.ai_client import default_llm_client
from app.services.work_intro import (
    POLICIES,
    IntroResult,
    PgWorkFacts,
    WorkFactsSource,
    WorkIntroService,
)

router = APIRouter(tags=["F1 작품"])  # works.router(/works) 아래에 붙는다

DEGRADED_NOTICE = "AI 소개를 만들지 못해 원문 줄거리와 촬영지를 보여 드려요"
NOT_ELIGIBLE_NOTICE = "KMDb와 연결된 작품만 AI 소개를 제공해요"


def get_intro_service() -> WorkIntroService:
    return WorkIntroService(default_llm_client())


def get_work_facts() -> WorkFactsSource:
    return PgWorkFacts()


Intro = Annotated[WorkIntroService, Depends(get_intro_service)]
Facts = Annotated[WorkFactsSource, Depends(get_work_facts)]
_RESPONSES = {404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]}


def _response(r: IntroResult) -> WorkIntroResponse:
    fallback = None
    if r.fallback is not None:
        fallback = IntroFallback(
            synopsis=r.fallback.synopsis,
            locations=[IntroLocation(name=loc.name, gu=loc.gu) for loc in r.fallback.locations],
        )
    notice = {"degraded": DEGRADED_NOTICE, "not_eligible": NOT_ELIGIBLE_NOTICE}.get(r.status)
    return WorkIntroResponse(
        status=r.status,
        spoiler=r.level,
        sentences=list(r.sentences),
        ai_generated=r.ai_generated,
        fallback=fallback,
        notice=notice,
        reason=r.reason,
        dropped_count=len(r.dropped),
    )


@router.post(
    "/{work_id}/intro",
    response_model=WorkIntroResponse,
    dependencies=[Depends(require_notice)],  # FR-701: 이용 안내를 확인한 요청만
    responses={
        **_RESPONSES,
        403: {"model": ErrorResponse, "description": "이용 안내 미확인(NOTICE_REQUIRED)"},
    },
    summary="API-004 AI 작품 소개",
)
async def introduce_work(
    work_id: int, intro: Intro, facts: Facts, body: WorkIntroRequest | None = None
) -> WorkIntroResponse:
    work = await facts.get(work_id)
    if work is None:
        raise ApiError(404, "WORK_NOT_FOUND", "작품을 찾을 수 없습니다")
    level = (body or WorkIntroRequest()).spoiler
    return _response(await intro.introduce(work, POLICIES[level]))

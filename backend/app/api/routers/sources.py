"""API-019 데이터 출처 (FR-705, UC-13, UI-09).

출처 대장(제공기관·데이터명·기준일·이용조건·저장 정책)과 오픈소스·AI 모델 라이선스, 이용 안내 버전.
로그인 없이 누구나 본다.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.errors import ERROR_RESPONSES
from app.api.schemas.sources import SourcesResponse
from app.core.notice import NOTICE_VERSION
from app.core.sources import LICENSES
from app.services.source_cards import ledger

router = APIRouter(tags=["공통"])

Ledger = Callable[[], Awaitable[list[dict[str, Any]]]]


def get_ledger() -> Ledger:
    return ledger


@router.get(
    "/sources",
    response_model=SourcesResponse,
    responses={503: ERROR_RESPONSES[503]},
    summary="API-019 데이터 출처",
)
async def get_sources(load: Annotated[Ledger, Depends(get_ledger)]) -> SourcesResponse:
    return SourcesResponse.model_validate(
        {
            "ledger": await load(),
            "notice_version": NOTICE_VERSION,
            "licenses": [{"name": n, "license": lic} for n, lic in LICENSES],
        }
    )

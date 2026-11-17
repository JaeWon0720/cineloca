"""API-007 선택 목록 (FR-202, FR-205, FR-310).

기준 위치 기본값·반경·구·군 중심점·장르 묶음은 고정값, 역·개봉연도 범위는 DB 값이다.
"""

from fastapi import APIRouter

from app.api.errors import ERROR_RESPONSES
from app.api.routers.locations import Service
from app.api.schemas.meta import MetaResponse

router = APIRouter(tags=["공통"])


@router.get(
    "/meta",
    response_model=MetaResponse,
    responses={503: ERROR_RESPONSES[503]},
    summary="API-007 선택 목록",
)
async def get_meta(service: Service) -> MetaResponse:
    return MetaResponse.model_validate(await service.meta())

"""API-007 선택 목록 (FR-202, FR-205, FR-310), API-026 인물 후보 (FR-310).

기준 위치 기본값·반경·구·군 중심점·장르 묶음은 고정값, 역·개봉연도 범위는 DB 값이다.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.errors import ERROR_RESPONSES
from app.api.routers.locations import Service
from app.api.schemas.meta import MetaResponse, PersonList

router = APIRouter(tags=["공통"])


@router.get(
    "/meta",
    response_model=MetaResponse,
    responses={503: ERROR_RESPONSES[503]},
    summary="API-007 선택 목록",
)
async def get_meta(service: Service) -> MetaResponse:
    return MetaResponse.model_validate(await service.meta())


@router.get(
    "/persons",
    response_model=PersonList,
    responses={422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
    summary="API-026 인물 후보(필터 패널)",
)
async def search_persons(
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=30, description="인물 이름(일부)")],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> PersonList:
    """F3 사전 대조와 같은 이름 찾기(정확 일치 → 트라이그램). 없으면 빈 목록."""
    return PersonList.model_validate({"items": await service.persons(q, limit)})

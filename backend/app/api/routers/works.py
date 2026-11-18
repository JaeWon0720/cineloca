"""F1 작품 아카이브 API: API-001 목록·검색·정렬, API-002 상세, API-003 관람등급 (FR-101~107)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.works import WorkDetail, WorkListResponse, WorkRating, WorkSort
from app.services.archive_service import PAGE_SIZE, ArchiveService, default_rating_provider

router = APIRouter(prefix="/works", tags=["F1 작품"])


def get_archive_service() -> ArchiveService:
    return ArchiveService(ratings=default_rating_provider())


Service = Annotated[ArchiveService, Depends(get_archive_service)]
_DB = {422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]}
_NOT_FOUND = {404: ERROR_RESPONSES[404], **_DB}


@router.get(
    "",
    response_model=WorkListResponse,
    responses=_DB,
    summary="API-001 작품 목록·검색·정렬",
)
async def list_works(
    service: Service,
    q: Annotated[str | None, Query(max_length=50, description="작품명·감독·배우")] = None,
    genre: Annotated[str | None, Query(max_length=30, description="KMDb 장르")] = None,
    sort: WorkSort = "title",
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = PAGE_SIZE,
) -> WorkListResponse:
    result = await service.list_works(q=q, genre=genre, sort=sort, page=page, size=size)
    return WorkListResponse.model_validate(result)


@router.get(
    "/{work_id}", response_model=WorkDetail, responses=_NOT_FOUND, summary="API-002 작품 상세"
)
async def get_work(work_id: int, service: Service) -> WorkDetail:
    detail = await service.get_work(work_id)
    if detail is None:
        raise ApiError(404, "WORK_NOT_FOUND", "작품을 찾을 수 없습니다")
    return WorkDetail.model_validate(detail)


@router.get(
    "/{work_id}/rating",
    response_model=WorkRating,
    responses=_NOT_FOUND,
    summary="API-003 KOBIS 관람등급 실시간",
)
async def get_rating(work_id: int, service: Service) -> WorkRating:
    """KOBIS를 3초 제한으로 부른다. 코드가 없거나 실패하면 rating=null(200)이다(FR-107)."""
    rating = await service.get_rating(work_id)
    if rating is None:
        raise ApiError(404, "WORK_NOT_FOUND", "작품을 찾을 수 없습니다")
    return WorkRating.model_validate(rating)

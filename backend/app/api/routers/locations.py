"""F2 지도 기반 탐색 API: API-005 근접·필터 검색, API-006 촬영지 상세 (FR-201~210)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.locations import (
    LocationDetail,
    LocationSearchRequest,
    LocationSearchResponse,
)
from app.services.map_service import MapService

router = APIRouter(prefix="/locations", tags=["F2 지도"])


def get_map_service() -> MapService:
    return MapService()


Service = Annotated[MapService, Depends(get_map_service)]


@router.post(
    "/search",
    response_model=LocationSearchResponse,
    responses={422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
    summary="API-005 근접·필터 검색",
)
async def search_locations(req: LocationSearchRequest, service: Service) -> LocationSearchResponse:
    """기준 위치 반경 안 촬영지를 거리순으로 최대 200곳 돌려준다(total은 전체 수).

    좌표 없는 촬영지는 빠진다(FR-210). 기준 위치는 요청 처리에만 쓰고 저장·로그하지 않는다(BR-10).
    """
    result = await service.search(
        lat=req.base.lat,
        lon=req.base.lon,
        radius_m=req.radius_m,
        genres=req.genres,
        year_from=req.year_from,
        year_to=req.year_to,
        gu=req.gu,
    )
    return LocationSearchResponse.model_validate(result)


@router.get(
    "/{location_id}",
    response_model=LocationDetail,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
    summary="API-006 촬영지 상세",
)
async def get_location(location_id: int, service: Service) -> LocationDetail:
    detail = await service.location(location_id)
    if detail is None:
        raise ApiError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다")
    return LocationDetail.model_validate(detail)

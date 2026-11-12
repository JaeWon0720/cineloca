"""F2 지도 기반 탐색 API: API-005 근접·필터 검색, API-006 촬영지 상세 (FR-201~210).

지금은 예시 응답(app.api.examples)을 돌려준다(#27). MapService 구현 이슈에서 실제 조회로 바꾼다.
"""

from fastapi import APIRouter

from app.api import examples
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.locations import (
    LocationDetail,
    LocationSearchRequest,
    LocationSearchResponse,
)

router = APIRouter(prefix="/locations", tags=["F2 지도"])


@router.post(
    "/search",
    response_model=LocationSearchResponse,
    responses={422: ERROR_RESPONSES[422]},
    summary="API-005 근접·필터 검색",
)
async def search_locations(req: LocationSearchRequest) -> LocationSearchResponse:
    """기준 위치 반경 안 촬영지를 거리순으로 돌려준다. 좌표 없는 촬영지는 빠진다(FR-210).

    기준 위치는 요청 처리에만 쓰고 저장·로그하지 않는다(BR-10).
    """
    return examples.search(req)


@router.get(
    "/{location_id}",
    response_model=LocationDetail,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422]},
    summary="API-006 촬영지 상세",
)
async def get_location(location_id: int) -> LocationDetail:
    detail = examples.location(location_id)
    if detail is None:
        raise ApiError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다")
    return detail

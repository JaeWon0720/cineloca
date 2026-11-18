"""F4 코스 주변 시설 추천 API: API-012 (FR-414~418, SD-03)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_notice
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.amenities import AmenityRequest, AmenityResponse
from app.api.schemas.common import ErrorResponse
from app.core.config import get_settings
from app.data.ai_gateway.tasks import FacilityConditions
from app.data.tourapi import TourApiClient
from app.services.ai_client import default_llm_client
from app.services.amenity_finder import AmenityFinder
from app.services.amenity_recommend import (
    AmenityRecommendService,
    LocationNotFound,
    LocationNotLocated,
)

router = APIRouter(prefix="/courses", tags=["F4 코스"])


def get_amenity_service() -> AmenityRecommendService:
    finder = AmenityFinder(TourApiClient(get_settings().tour_api_key))
    return AmenityRecommendService(default_llm_client(), finder)


Service = Annotated[AmenityRecommendService, Depends(get_amenity_service)]


@router.post(
    "/amenities",
    response_model=AmenityResponse,
    dependencies=[Depends(require_notice)],
    responses={
        403: {"model": ErrorResponse, "description": "이용 안내 미확인(NOTICE_REQUIRED, FR-701)"},
        404: ERROR_RESPONSES[404],
        409: {"model": ErrorResponse, "description": "좌표 없는 촬영지(LOCATION_NOT_LOCATED)"},
        422: ERROR_RESPONSES[422],
        503: ERROR_RESPONSES[503],
    },
    summary="API-012 코스 주변 시설 추천",
)
async def recommend_amenities(req: AmenityRequest, service: Service) -> AmenityResponse:
    """구간 중간 지점 1km 카페·음식점, 마지막 촬영지 1km 숙박을 구간마다 최대 3곳 추천한다.

    AI가 실패하면 조건은 직접 고르기, 이유는 분류·거리 템플릿으로 바뀌고 추천은 그대로다(FR-418).
    시설 조회가 실패한 구간은 안내만 하고 코스는 그대로 둔다.
    """
    selected = FacilityConditions(**req.selected.model_dump()) if req.selected is not None else None
    try:
        result = await service.recommend(req.location_ids, text=req.text, selected=selected)
    except LocationNotFound:
        raise ApiError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다") from None
    except LocationNotLocated:
        raise ApiError(
            409, "LOCATION_NOT_LOCATED", "위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요."
        ) from None
    return AmenityResponse.model_validate(result)

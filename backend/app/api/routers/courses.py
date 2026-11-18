"""F4 코스 API: API-011 나만의 코스 경로 (FR-403~405·413, BR-12·13)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.common import ErrorResponse
from app.api.schemas.courses import CoursePlan, PlanRequest
from app.services.course_service import (
    CourseService,
    InvalidStops,
    LocationNotFound,
    LocationNotLocated,
    StartSpec,
    StationNotFound,
)

router = APIRouter(prefix="/courses", tags=["F4 코스"])


def get_course_service() -> CourseService:
    return CourseService()


Service = Annotated[CourseService, Depends(get_course_service)]


@router.post(
    "/plan",
    response_model=CoursePlan,
    responses={
        404: ERROR_RESPONSES[404],
        409: {"model": ErrorResponse, "description": "좌표 없는 촬영지(LOCATION_NOT_LOCATED)"},
        422: ERROR_RESPONSES[422],
        503: ERROR_RESPONSES[503],
    },
    summary="API-011 나만의 코스 경로",
)
async def plan_course(req: PlanRequest, service: Service) -> CoursePlan:
    """방문 순서를 최적화(최근접 이웃 + 2-opt)하거나 받은 순서대로 이동·체류·총 시간을 계산한다.

    현재 위치는 계산에만 쓰고 저장·로그·응답하지 않는다(BR-10).
    """
    s = req.start
    start = StartSpec(s.type, s.lat, s.lon, int(s.id) if s.type == "location" else s.id)
    try:
        result = await service.plan_custom(start, req.location_ids, req.fixed_order)
    except InvalidStops as e:
        raise ApiError(422, "INVALID_REQUEST", str(e)) from None
    except LocationNotFound:
        raise ApiError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다") from None
    except LocationNotLocated:
        raise ApiError(
            409, "LOCATION_NOT_LOCATED", "위치가 확인되지 않은 촬영지는 코스에 넣을 수 없어요."
        ) from None
    except StationNotFound:
        raise ApiError(404, "STATION_NOT_FOUND", "역을 찾을 수 없습니다") from None
    return CoursePlan.model_validate(result)

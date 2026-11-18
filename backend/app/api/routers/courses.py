"""F4 코스 API: API-009 추천 코스 목록, API-010 상세, API-011 나만의 코스 경로 (FR-401~405·413)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.common import ErrorResponse
from app.api.schemas.courses import CourseDetail, CourseList, CoursePlan, PlanRequest, Theme
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


@router.get(
    "",
    response_model=CourseList,
    responses={422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
    summary="API-009 추천 코스 목록",
)
async def list_courses(
    service: Service, theme: Annotated[Theme | None, Query(description="작품·배우·지역")] = None
) -> CourseList:
    """관리자가 승인한 추천 코스만, 장소 수·예상 총 소요 시간·총 이동 거리와 함께(FR-401)."""
    return CourseList.model_validate(await service.list_recommended(theme))


@router.get(
    "/{course_id}",
    response_model=CourseDetail,
    responses={404: ERROR_RESPONSES[404], 422: ERROR_RESPONSES[422], 503: ERROR_RESPONSES[503]},
    summary="API-010 추천 코스 상세",
)
async def get_course(course_id: int, service: Service) -> CourseDetail:
    """동선·방문 순서·구간 수단과 시간·체류 시간·총 소요 시간(FR-402)."""
    try:
        detail = await service.get_course(course_id)
    except LocationNotLocated:
        detail = None  # 데이터 갱신으로 좌표가 빠진 장소가 생기면 목록과 같이 숨긴다
    if detail is None:
        raise ApiError(404, "COURSE_NOT_FOUND", "추천 코스를 찾을 수 없습니다")
    return CourseDetail.model_validate(detail)

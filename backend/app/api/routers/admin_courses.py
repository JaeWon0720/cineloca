"""추천 코스 관리 API: API-024 후보 생성·수정·승인 (FR-614, UI-12). 관리자 로그인(API-020) 필요.

- GET    /api/admin/courses?status=draft|approved&theme=work|person|district  목록(승인 전 포함)
- POST   /api/admin/courses  {theme, theme_ref, max_stops}  후보 생성(승인 전, 201)
- GET    /api/admin/courses/{id}  상세(사용자 화면과 같은 계산 + 상태)
- PATCH  /api/admin/courses/{id}  제목·장소·순서·체류·출발역 수정, status로 승인·승인 취소
- DELETE /api/admin/courses/{id}  승인 전 후보 삭제
승인된 코스만 API-009·010(추천 코스 목록·상세)에 나온다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from app.api.deps import require_admin
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.admin_courses import (
    AdminCourseDetail,
    AdminCourseList,
    CandidateRequest,
    CoursePatch,
    CourseStatus,
)
from app.api.schemas.common import ErrorResponse
from app.api.schemas.courses import Theme
from app.services.course_admin import CourseAdminError, CourseAdminService, StopEdit

router = APIRouter(prefix="/admin/courses", tags=["관리자"], dependencies=[Depends(require_admin)])


def get_course_admin() -> CourseAdminService:
    return CourseAdminService()


Service = Annotated[CourseAdminService, Depends(get_course_admin)]
CourseId = Annotated[int, Path(ge=1)]
_ERR = {"model": ErrorResponse}
_RESPONSES: dict[int | str, dict] = {
    400: {**_ERR, "description": "처리할 수 없는 값(INVALID_REQUEST)"},
    401: {**_ERR, "description": "관리자 로그인 필요(ADMIN_REQUIRED)"},
    404: {**_ERR, "description": "코스·작품·인물·촬영지·역 없음"},
    409: {**_ERR, "description": "좌표 없는 촬영지·촬영지 부족·승인된 코스 삭제"},
    422: ERROR_RESPONSES[422],
    503: ERROR_RESPONSES[503],
}


def _api(e: CourseAdminError) -> ApiError:
    return ApiError(e.status, e.code, e.message)


@router.get("", response_model=AdminCourseList, responses=_RESPONSES, summary="API-024 목록")
async def list_courses(
    service: Service, status: CourseStatus | None = None, theme: Theme | None = None
) -> AdminCourseList:
    return AdminCourseList.model_validate(await service.list(status, theme))


@router.post(
    "",
    status_code=201,
    response_model=AdminCourseDetail,
    responses=_RESPONSES,
    summary="API-024 후보 생성",
)
async def create_candidate(body: CandidateRequest, service: Service) -> AdminCourseDetail:
    """테마에 맞는 촬영지를 모아 이동 시간이 짧은 순서로 늘어놓은 후보를 만든다."""
    try:
        detail = await service.create_candidate(body.theme, body.theme_ref.strip(), body.max_stops)
    except CourseAdminError as e:
        raise _api(e) from None
    return AdminCourseDetail.model_validate(detail)


@router.get(
    "/{course_id}", response_model=AdminCourseDetail, responses=_RESPONSES, summary="API-024 상세"
)
async def get_course(course_id: CourseId, service: Service) -> AdminCourseDetail:
    try:
        return AdminCourseDetail.model_validate(await service.get(course_id))
    except CourseAdminError as e:
        raise _api(e) from None


@router.patch(
    "/{course_id}",
    response_model=AdminCourseDetail,
    responses=_RESPONSES,
    summary="API-024 수정·승인",
)
async def patch_course(
    body: CoursePatch, course_id: CourseId, service: Service
) -> AdminCourseDetail:
    sent = body.model_fields_set
    try:
        detail = None
        if sent & {"title", "stops", "start_station_id"}:
            detail = await service.update(
                course_id,
                title=body.title,
                stops=[StopEdit(s.location_id, s.stay_min) for s in body.stops]
                if body.stops is not None
                else None,
                start_station_id=body.start_station_id,
                clear_start="start_station_id" in sent and body.start_station_id is None,
            )
        if body.status is not None:
            detail = await service.set_status(course_id, body.status == "approved")
        if detail is None:
            detail = await service.get(course_id)
    except CourseAdminError as e:
        raise _api(e) from None
    return AdminCourseDetail.model_validate(detail)


@router.delete("/{course_id}", status_code=204, responses=_RESPONSES, summary="API-024 후보 삭제")
async def delete_course(course_id: CourseId, service: Service) -> Response:
    try:
        await service.delete_draft(course_id)
    except CourseAdminError as e:
        raise _api(e) from None
    return Response(status_code=204)

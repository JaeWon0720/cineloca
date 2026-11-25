"""관리자 검수 큐·촬영지 속성 API: API-022 (FR-608·609, BR-04), API-023 (FR-610, BR-06·07).

- GET   /api/admin/reviews?status=pending|handled|all&kind=tag|match&page=1&size=20
- PATCH /api/admin/reviews/{id}  승인·수정·반려. 태그는 촬영지에 바로, 매칭은 다음 동기화에 반영
- GET   /api/admin/locations/{id}  편집할 속성
- PATCH /api/admin/locations/{id}  사유지·주거지·학교, 안전 안내, 인증 반경(50~300m), 인증 제외
모두 관리자 로그인(API-020)이 필요하다.
오류: 400 INVALID_REQUEST · 404 REVIEW_NOT_FOUND·LOCATION_NOT_FOUND
· 409 REVIEW_ALREADY_HANDLED·REVIEW_TARGET_GONE.
"""

from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import require_admin
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.admin_review import (
    AdminLocationAttrs,
    AdminLocationPatch,
    ReviewAction,
    ReviewItem,
    ReviewListResponse,
)
from app.api.schemas.common import ErrorResponse
from app.services.admin_review import AdminReviewService, ReviewError

router = APIRouter(prefix="/admin", tags=["관리자"], dependencies=[Depends(require_admin)])

GONE_NAME = "(삭제됨)"


def get_review_service() -> AdminReviewService:
    return AdminReviewService()


Service = Annotated[AdminReviewService, Depends(get_review_service)]
_ERR = {"model": ErrorResponse}
_RESPONSES: dict[int | str, dict] = {
    400: {**_ERR, "description": "처리할 수 없는 값(INVALID_REQUEST)"},
    401: {**_ERR, "description": "관리자 로그인 필요(ADMIN_REQUIRED)"},
    404: {**_ERR, "description": "검수 항목·촬영지 없음"},
    409: {**_ERR, "description": "이미 처리됨·대상 없어짐"},
    422: ERROR_RESPONSES[422],
    503: ERROR_RESPONSES[503],
}


def _api_error(e: ReviewError) -> ApiError:
    return ApiError(e.status, e.code, e.message)


def _item(row) -> ReviewItem:  # noqa: ANN001 - data.reviews.ReviewRow
    d = asdict(row)
    d.pop("target_exists")
    d["target_name"] = d["target_name"] or GONE_NAME
    return ReviewItem.model_validate(d)


@router.get(
    "/reviews", response_model=ReviewListResponse, responses=_RESPONSES, summary="API-022 검수 큐"
)
async def list_reviews(
    service: Service,
    status: Literal["pending", "handled", "all"] = "pending",
    kind: Literal["tag", "match"] | None = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ReviewListResponse:
    r = await service.list(status, kind, page, size)
    return ReviewListResponse(**{**r, "items": [_item(x) for x in r["items"]]})


@router.patch(
    "/reviews/{review_id}",
    response_model=ReviewItem,
    responses=_RESPONSES,
    summary="API-022 검수 처리",
)
async def handle_review(
    body: ReviewAction, service: Service, review_id: Annotated[int, Path(ge=1)]
) -> ReviewItem:
    try:
        row = await service.handle(review_id, body.action, body.tags, body.kmdb_id)
    except ReviewError as e:
        raise _api_error(e) from None
    return _item(row)


@router.get(
    "/locations/{location_id}",
    response_model=AdminLocationAttrs,
    responses=_RESPONSES,
    summary="API-023 촬영지 속성",
)
async def get_location(
    service: Service, location_id: Annotated[int, Path(ge=1)]
) -> AdminLocationAttrs:
    try:
        return AdminLocationAttrs.model_validate(asdict(await service.location(location_id)))
    except ReviewError as e:
        raise _api_error(e) from None


@router.patch(
    "/locations/{location_id}",
    response_model=AdminLocationAttrs,
    responses=_RESPONSES,
    summary="API-023 촬영지 속성 편집",
)
async def patch_location(
    body: AdminLocationPatch, service: Service, location_id: Annotated[int, Path(ge=1)]
) -> AdminLocationAttrs:
    try:
        attrs = await service.update_location(location_id, body.model_dump(exclude_unset=True))
    except ReviewError as e:
        raise _api_error(e) from None
    return AdminLocationAttrs.model_validate(asdict(attrs))

"""F5 방문 인증 API: API-016 인증, API-017 토큰 확인, API-018 완주 배지 (FR-501~508, ADR-009).

서버는 위치·스탬프를 저장하지 않는다. 토큰은 브라우저가 보관하고 필요할 때 다시 보낸다.
"""

import logging
from dataclasses import asdict
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.common import ErrorResponse
from app.api.schemas.stamps import (
    BadgeRequest,
    BadgeResponse,
    StampRequest,
    StampResponse,
    VerifyRequest,
    VerifyResponse,
)
from app.core.config import get_settings
from app.services.stamp_service import (
    CertExcluded,
    CourseNotFound,
    GeoFix,
    LocationNotFound,
    StampService,
    StampUnavailable,
)
from app.support.tokens import TokenSigner

logger = logging.getLogger(__name__)
router = APIRouter(tags=["F5 방문 인증"])

# ERR-009: 인증 제외 이유(BR-06)
EXCLUDED_MESSAGES = {
    "no_coordinates": "위치가 확인되지 않은 촬영지라 방문 인증을 할 수 없어요.",
    "private": "사유지라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.",
    "residential": "주민이 사는 곳이라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.",
    "school": "학교라 방문 인증을 하지 않아요. 외부에서만 관람해 주세요.",
    "excluded": "방문 인증 대상에서 제외된 촬영지예요.",
}
_RESPONSES: dict[int | str, dict] = {
    422: ERROR_RESPONSES[422],
    503: {
        "model": ErrorResponse,
        "description": "서명 키 없음(STAMP_UNAVAILABLE) 또는 DB 연결 실패",
    },
}


@lru_cache
def _signer(secret: str | None) -> TokenSigner | None:
    if not secret:
        logger.warning("STAMP_SIGNING_KEY가 없어 방문 인증을 쓸 수 없습니다")
        return None
    try:
        return TokenSigner(secret)
    except ValueError as e:
        logger.warning("%s", e)
        return None


def get_stamp_service() -> StampService:
    return StampService(_signer(get_settings().stamp_signing_key))


Service = Annotated[StampService, Depends(get_stamp_service)]


def _unavailable() -> ApiError:
    return ApiError(
        503, "STAMP_UNAVAILABLE", "지금은 방문 인증을 할 수 없어요. 잠시 후 다시 시도해 주세요."
    )


@router.post(
    "/stamps",
    response_model=StampResponse,
    responses={
        **_RESPONSES,
        404: ERROR_RESPONSES[404],
        409: {"model": ErrorResponse, "description": "인증 제외 촬영지(CERT_EXCLUDED, ERR-009)"},
    },
    summary="API-016 방문 인증",
)
async def certify(req: StampRequest, service: Service) -> StampResponse:
    """정확도 50m 이하 + 인증 반경 안이면 스탬프 토큰(BR-07). 실패는 200 ok=false(ERR-008)."""
    fix = GeoFix(req.lat, req.lon, req.accuracy_m)  # 이 요청 안에서만 쓴다
    try:
        result = await service.certify(req.location_id, fix, req.held)
    except StampUnavailable:
        raise _unavailable() from None
    except LocationNotFound:
        raise ApiError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다") from None
    except CertExcluded as e:
        raise ApiError(409, "CERT_EXCLUDED", EXCLUDED_MESSAGES[e.reason]) from None
    return StampResponse.model_validate(asdict(result))


@router.post(
    "/stamps/verify",
    response_model=VerifyResponse,
    responses=_RESPONSES,
    summary="API-017 스탬프·배지 토큰 확인",
)
async def verify(req: VerifyRequest, service: Service) -> VerifyResponse:
    try:
        return VerifyResponse.model_validate(await service.verify(req.tokens))
    except StampUnavailable:
        raise _unavailable() from None


@router.post(
    "/badges",
    response_model=BadgeResponse,
    responses={**_RESPONSES, 404: ERROR_RESPONSES[404]},
    summary="API-018 완주 배지",
)
async def issue_badge(req: BadgeRequest, service: Service) -> BadgeResponse:
    """코스의 인증 대상 장소(2곳 이상)를 모두 인증했으면 배지 토큰을 준다(BR-09)."""
    try:
        result = await service.issue_badge(
            req.stamps, course_id=req.course.id, location_ids=req.course.location_ids
        )
    except StampUnavailable:
        raise _unavailable() from None
    except CourseNotFound:
        raise ApiError(404, "COURSE_NOT_FOUND", "추천 코스를 찾을 수 없습니다") from None
    return BadgeResponse.model_validate(asdict(result))

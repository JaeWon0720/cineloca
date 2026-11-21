"""관리자 API: API-020 관리자 로그인·로그아웃·상태 (FR-615, SDD 3.2).

- POST   /api/admin/session  비밀번호 확인 → 세션 쿠키(HttpOnly·Secure·SameSite=Strict, 2시간)
- GET    /api/admin/session  지금 쿠키가 유효한지(관리자 화면이 처음 열릴 때)
- DELETE /api/admin/session  로그아웃(쿠키 삭제, 그 토큰은 만료까지 거부)
오류: 401 ADMIN_AUTH_FAILED(남은 시도 안내) · 401 ADMIN_REQUIRED · 429 ADMIN_LOCKED(retry_after)
· 503 ADMIN_UNAVAILABLE(해시 미설정). 비밀번호는 응답·로그 어디에도 남기지 않는다.
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Response

from app.api.deps import ADMIN_COOKIE, AdminAuthDep
from app.api.errors import ERROR_RESPONSES, ApiError
from app.api.schemas.admin import AdminLoginRequest, AdminSessionResponse
from app.api.schemas.common import ErrorResponse
from app.services.admin_auth import SESSION_TTL_S, AdminBadPassword, AdminLocked

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["관리자"])

COOKIE_PATH = "/api/admin"
_RESPONSES: dict[int | str, dict] = {
    401: {"model": ErrorResponse, "description": "비밀번호 틀림·로그인 필요"},
    422: ERROR_RESPONSES[422],
    429: {"model": ErrorResponse, "description": "5회 연속 실패로 10분 잠금(ADMIN_LOCKED)"},
    503: {"model": ErrorResponse, "description": "관리자 비밀번호 미설정(ADMIN_UNAVAILABLE)"},
}
AdminCookie = Annotated[str | None, Cookie(alias=ADMIN_COOKIE, max_length=200)]


def _at(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


@router.post(
    "/session", response_model=AdminSessionResponse, responses=_RESPONSES, summary="API-020 로그인"
)
def login(body: AdminLoginRequest, auth: AdminAuthDep, response: Response) -> AdminSessionResponse:
    # def(동기): PBKDF2 계산이 이벤트 루프를 막지 않게 스레드 풀에서 돈다
    try:
        session = auth.login(body.password)
    except AdminLocked as e:
        logger.warning("관리자 로그인 5회 연속 실패로 잠금")
        raise ApiError(
            429,
            "ADMIN_LOCKED",
            "비밀번호를 5회 틀려 10분 동안 로그인할 수 없습니다.",
            retry_after=e.retry_after,
        ) from None
    except AdminBadPassword as e:
        logger.info("관리자 로그인 실패(남은 시도 %d회)", e.remaining)
        raise ApiError(
            401, "ADMIN_AUTH_FAILED", f"비밀번호가 맞지 않습니다. 남은 시도 {e.remaining}회"
        ) from None
    response.set_cookie(
        ADMIN_COOKIE,
        session.token,
        max_age=SESSION_TTL_S,
        path=COOKIE_PATH,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return AdminSessionResponse(expires_at=_at(session.expires_at))


@router.get(
    "/session", response_model=AdminSessionResponse, responses=_RESPONSES, summary="로그인 상태"
)
async def session_status(auth: AdminAuthDep, token: AdminCookie = None) -> AdminSessionResponse:
    expires = auth.verify(token)
    if expires is None:
        raise ApiError(401, "ADMIN_REQUIRED", "관리자 로그인이 필요합니다.")
    return AdminSessionResponse(expires_at=_at(expires))


@router.delete("/session", status_code=204, responses=_RESPONSES, summary="로그아웃")
async def logout(auth: AdminAuthDep, response: Response, token: AdminCookie = None) -> Response:
    auth.logout(token)
    response.delete_cookie(ADMIN_COOKIE, path=COOKIE_PATH, secure=True, httponly=True)
    response.status_code = 204
    return response

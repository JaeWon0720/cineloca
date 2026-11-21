"""여러 라우터가 함께 쓰는 의존성: 이용 안내 확인(FR-701), 관리자 인증(FR-615).

AI 클라이언트는 app/services/ai_client.py의 default_llm_client() 하나를 쓴다(#96).
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends, Header

from app.api.errors import ApiError
from app.core.config import get_settings
from app.core.notice import NOTICE_VERSION
from app.services.admin_auth import AdminAuth

logger = logging.getLogger(__name__)

ADMIN_COOKIE = "cineloca_admin"


def require_notice(
    x_notice_version: Annotated[str | None, Header(max_length=20)] = None,
) -> None:
    """AI를 쓰는 요청(API-004·012·013)은 확인한 이용 안내 버전이 지금 버전과 같아야 한다(FR-701)."""
    if x_notice_version != NOTICE_VERSION:
        raise ApiError(403, "NOTICE_REQUIRED", "이용 안내를 확인한 뒤 다시 시도해 주세요.")


@lru_cache
def _auth_for(stored_hash: str) -> AdminAuth | None:
    try:
        return AdminAuth(stored_hash)
    except ValueError:
        logger.warning("ADMIN_PASSWORD_HASH 형식이 맞지 않아 관리 기능을 끕니다")
        return None


def get_admin_auth() -> AdminAuth:
    """관리자 인증기. 해시가 없거나 형식이 틀리면 관리 API만 503(다른 기능은 그대로)."""
    stored = get_settings().admin_password_hash
    auth = _auth_for(stored) if stored else None
    if auth is None:
        raise ApiError(503, "ADMIN_UNAVAILABLE", "관리자 비밀번호가 설정되지 않았습니다.")
    return auth


AdminAuthDep = Annotated[AdminAuth, Depends(get_admin_auth)]


def require_admin(
    auth: AdminAuthDep,
    token: Annotated[str | None, Cookie(alias=ADMIN_COOKIE, max_length=200)] = None,
) -> None:
    """관리 API(API-021~024)는 API-020이 발급한 세션 쿠키가 있어야 한다(FR-615).

    라우터에 dependencies=[Depends(require_admin)]로 붙인다.
    """
    if auth.verify(token) is None:
        raise ApiError(401, "ADMIN_REQUIRED", "관리자 로그인이 필요합니다.")

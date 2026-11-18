"""여러 라우터가 함께 쓰는 의존성: 이용 안내 확인(FR-701).

AI 클라이언트는 app/services/ai_client.py의 default_llm_client() 하나를 쓴다(#96).
"""

from typing import Annotated

from fastapi import Header

from app.api.errors import ApiError
from app.core.notice import NOTICE_VERSION


def require_notice(
    x_notice_version: Annotated[str | None, Header(max_length=20)] = None,
) -> None:
    """AI를 쓰는 요청(API-004·012·013)은 확인한 이용 안내 버전이 지금 버전과 같아야 한다(FR-701)."""
    if x_notice_version != NOTICE_VERSION:
        raise ApiError(403, "NOTICE_REQUIRED", "이용 안내를 확인한 뒤 다시 시도해 주세요.")

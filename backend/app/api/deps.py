"""여러 라우터가 함께 쓰는 의존성: AI 클라이언트, 이용 안내 확인(FR-701)."""

from functools import lru_cache
from typing import Annotated

from fastapi import Header

from app.api.errors import ApiError
from app.core.notice import NOTICE_VERSION
from app.data.ai_gateway import (
    AISettings,
    LLMClient,
    PgAICache,
    PgUsageCounter,
    build_llm_client,
)


@lru_cache
def get_llm_client() -> LLMClient:
    """AI_PROVIDER로 고른 공급자 체인. 호출 수(Gemini 한도)와 응답 캐시는 DB에 둔다(ADR-013)."""
    return build_llm_client(AISettings.from_env(), usage=PgUsageCounter(), cache=PgAICache())


def require_notice(
    x_notice_version: Annotated[str | None, Header(max_length=20)] = None,
) -> None:
    """AI를 쓰는 요청(API-004·012·013)은 확인한 이용 안내 버전이 지금 버전과 같아야 한다(FR-701)."""
    if x_notice_version != NOTICE_VERSION:
        raise ApiError(403, "NOTICE_REQUIRED", "이용 안내를 확인한 뒤 다시 시도해 주세요.")

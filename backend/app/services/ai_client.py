"""API가 함께 쓰는 AI Gateway 클라이언트 (COMP-08, ADR-004).

환경변수(AI_PROVIDER·GEMINI_API_KEY 등)로 공급자를 고르고, 일일 호출 한도(NFR-018)와
24시간 응답 캐시(AI-REQ-15)를 붙인다. 설정이 틀리면 AI 없이(provider=none) 시작해
각 기능이 강등 화면으로 돈다. 프로세스에 하나만 만든다.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.data.ai_gateway import (
    AISettings,
    LLMClient,
    PgAICache,
    PgUsageCounter,
    build_llm_client,
)

logger = logging.getLogger(__name__)


@lru_cache
def default_llm_client() -> LLMClient:
    try:
        settings = AISettings.from_env()
        return build_llm_client(settings, usage=PgUsageCounter(), cache=PgAICache())
    except ValueError:
        logger.warning("AI 설정이 올바르지 않아 AI 없이 시작합니다")
        return build_llm_client(AISettings(provider="none"))

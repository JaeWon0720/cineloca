"""AI Gateway (COMP-08): 외부 AI 호출의 단일 출입구.

업무 서비스 → 마스킹(COMP-10) → LLMClient(COMP-08) → 근거 대조(COMP-09)
한 길로만 AI를 부른다(SDD 2.3).

    client: LLMClient = build_llm_client(AISettings.from_env())  # AI_PROVIDER로 공급자 선택
    try:
        result = await client.generate("extract_query", {"query": mask(text)}, deadline_s=8)
        conditions = result.data  # QueryConditions (스키마 검증 완료)
    except AIUnavailable as e:
        ...  # 강등 응답(필터 패널 등). e.error_code, e.attempts는 로그에만

DB를 쓰는 곳(API·배치)은 호출 수·Gemini 일일 한도와 응답 캐시를 붙인다:
    build_llm_client(settings, usage=PgUsageCounter(), cache=PgAICache())

남은 것(스프린트 2): 작업별 프롬프트(F1·F3·F4 요약), config/ai.yaml 로딩(지금은 환경변수).
"""

from app.data.ai_gateway.cache import ResponseCache, cache_key
from app.data.ai_gateway.chain import ProviderChain
from app.data.ai_gateway.circuit import CircuitBreaker
from app.data.ai_gateway.contracts import (
    Attempt,
    Embedder,
    EvidenceInput,
    LLMClient,
    LLMProvider,
    LLMResult,
    MaskedInput,
    Prompt,
)
from app.data.ai_gateway.errors import AIUnavailable, ProviderError
from app.data.ai_gateway.factory import AISettings, build_embedder, build_llm_client
from app.data.ai_gateway.prompts import DefaultPromptBuilder, PromptBuilder
from app.data.ai_gateway.providers import GeminiProvider, OllamaEmbedder, OllamaProvider
from app.data.ai_gateway.quota import DailyQuota, UsageCounter
from app.data.ai_gateway.store import PgAICache, PgUsageCounter
from app.data.ai_gateway.tasks import TASKS, TaskSpec

__all__ = [
    "TASKS",
    "AISettings",
    "AIUnavailable",
    "Attempt",
    "CircuitBreaker",
    "DailyQuota",
    "DefaultPromptBuilder",
    "Embedder",
    "EvidenceInput",
    "GeminiProvider",
    "LLMClient",
    "LLMProvider",
    "LLMResult",
    "MaskedInput",
    "OllamaEmbedder",
    "OllamaProvider",
    "PgAICache",
    "PgUsageCounter",
    "Prompt",
    "PromptBuilder",
    "ProviderChain",
    "ProviderError",
    "ResponseCache",
    "TaskSpec",
    "UsageCounter",
    "build_embedder",
    "build_llm_client",
    "cache_key",
]

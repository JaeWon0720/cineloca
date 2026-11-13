"""AI Gateway (COMP-08): 외부 AI 호출의 단일 출입구.

업무 서비스 → 마스킹(COMP-10) → LLMClient(COMP-08) → 근거 대조(COMP-09)
한 길로만 AI를 부른다(SDD 2.3).

    client: LLMClient = ProviderChain(providers, DefaultPromptBuilder())
    try:
        result = await client.generate("extract_query", {"query": mask(text)}, deadline_s=8)
        conditions = result.data  # QueryConditions (스키마 검증 완료)
    except AIUnavailable as e:
        ...  # 강등 응답(필터 패널 등). e.error_code, e.attempts는 로그에만

초안 범위(스프린트 2에서 채울 것): 실제 Ollama·Gemini 공급자, Embedder 구현, 응답 캐시(AI-REQ-15),
호출 수 기록(usage_counter), 작업별 프롬프트(extract_query 외), config/ai.yaml 로딩.
"""

from app.data.ai_gateway.chain import ProviderChain
from app.data.ai_gateway.circuit import CircuitBreaker
from app.data.ai_gateway.contracts import (
    Attempt,
    Embedder,
    LLMClient,
    LLMProvider,
    LLMResult,
    MaskedInput,
    Prompt,
)
from app.data.ai_gateway.errors import AIUnavailable, ProviderError
from app.data.ai_gateway.prompts import DefaultPromptBuilder, PromptBuilder
from app.data.ai_gateway.tasks import TASKS, TaskSpec

__all__ = [
    "TASKS",
    "AIUnavailable",
    "Attempt",
    "CircuitBreaker",
    "DefaultPromptBuilder",
    "Embedder",
    "LLMClient",
    "LLMProvider",
    "LLMResult",
    "MaskedInput",
    "Prompt",
    "PromptBuilder",
    "ProviderChain",
    "ProviderError",
    "TaskSpec",
]

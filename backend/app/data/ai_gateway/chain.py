"""대체 체인 LLMClient 구현 (ADR-003·004, AI-REQ-02·04, NFR-002).

공급자 순서: 작업 설정 순서(기본 Ollama → Gemini) → 모두 실패하면 AIUnavailable(강등).
- 공급자 오류(시간 초과·429·5xx): 차단기에 실패 기록 후 다음 공급자.
  429이고 Retry-After가 2초 이하면 같은 공급자에 1번 다시.
- 스키마 위반(허용 값 밖 포함): 같은 공급자에 1번 다시 → 다음 공급자.
  공급자는 응답했으므로 차단기 실패로 세지 않는다.
- 차단기가 열린 공급자, 한도를 넘은 공급자(allow_provider=False)는 건너뛴다.
- 남은 마감 시간이 min_attempt_s보다 적으면 더 시도하지 않고 바로 강등(AI_DEADLINE).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import ValidationError

from app.data.ai_gateway.circuit import CircuitBreaker
from app.data.ai_gateway.contracts import Attempt, LLMProvider, LLMResult, MaskedInput
from app.data.ai_gateway.errors import RATE_LIMITED, TIMEOUT, UPSTREAM, AIUnavailable, ProviderError
from app.data.ai_gateway.prompts import PromptBuilder
from app.data.ai_gateway.tasks import TASKS, TaskSpec

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def _strip_fences(raw: str) -> str:
    """```json ... ``` 으로 감싼 응답을 벗긴다."""
    match = _FENCE.match(raw)
    return match.group(1) if match else raw


class ProviderChain:
    def __init__(
        self,
        providers: Mapping[str, LLMProvider],
        prompts: PromptBuilder,
        tasks: Mapping[str, TaskSpec] = TASKS,
        *,
        allow_provider: Callable[[str], bool] = lambda _name: True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        min_attempt_s: float = 1.0,
        max_retry_after_s: float = 2.0,
    ) -> None:
        self._providers = dict(providers)
        self._prompts = prompts
        self._tasks = dict(tasks)
        self._allow_provider = allow_provider  # Gemini 일일 한도 100%면 False (NFR-005·018)
        self._clock = clock
        self._sleep = sleep
        self._min_attempt_s = min_attempt_s
        self._max_retry_after_s = max_retry_after_s
        self._breakers = {name: CircuitBreaker(clock=clock) for name in self._providers}

    def breaker(self, provider: str) -> CircuitBreaker:
        return self._breakers[provider]

    async def generate(self, task: str, inputs: Mapping[str, Any], deadline_s: float) -> LLMResult:
        spec = self._tasks.get(task)
        if spec is None:
            raise ValueError(f"등록되지 않은 AI 작업: {task}")
        for field in spec.user_text_fields:
            if not isinstance(inputs.get(field), MaskedInput):
                raise TypeError(
                    f"{task}.{field}는 마스킹한 문장(MaskedText)이어야 합니다 (AI-REQ-03)"
                )
        prompt = self._prompts.build(spec, inputs)

        start = self._clock()
        deadline = start + deadline_s
        attempts: list[Attempt] = []
        error_code = "AI_UNAVAILABLE"

        for name in spec.providers:
            provider = self._providers.get(name)
            if provider is None:  # 이 환경에 없는 공급자(예: 팀원 PC에는 Ollama 없음)
                continue
            if not self._allow_provider(name):
                attempts.append(Attempt(name, "skipped_quota", 0))
                continue
            breaker = self._breakers[name]
            if not breaker.allow():
                attempts.append(Attempt(name, "circuit_open", 0))
                continue

            schema_retries = 1
            rate_retries = 1
            while True:
                remaining = deadline - self._clock()
                if remaining < self._min_attempt_s:
                    attempts.append(Attempt(name, "deadline", 0))
                    raise self._degrade(task, "AI_DEADLINE", attempts)

                called = self._clock()
                try:
                    timeout = min(spec.timeout_s, remaining)
                    raw = await asyncio.wait_for(
                        provider.complete(prompt, timeout_s=timeout), timeout=timeout
                    )
                except Exception as exc:  # ProviderError·시간 초과·예상 못 한 오류 모두 강등 대상
                    error = _as_provider_error(exc)
                    attempts.append(Attempt(name, error.kind, self._ms_since(called)))
                    breaker.record_failure()
                    error_code = "AI_UNAVAILABLE"
                    retry_after = error.retry_after_s
                    if (
                        error.kind == RATE_LIMITED
                        and rate_retries
                        and retry_after is not None
                        and retry_after <= self._max_retry_after_s
                    ):
                        rate_retries -= 1
                        await self._sleep(retry_after)
                        continue
                    break  # 다음 공급자

                breaker.record_success()
                try:
                    data = spec.output.model_validate_json(_strip_fences(raw))
                except ValidationError:
                    attempts.append(Attempt(name, "schema", self._ms_since(called)))
                    error_code = "AI_SCHEMA"
                    if schema_retries:
                        schema_retries -= 1
                        continue
                    break  # 다음 공급자

                attempts.append(Attempt(name, "ok", self._ms_since(called)))
                return LLMResult(task, data, name, self._ms_since(start), tuple(attempts))

        raise self._degrade(task, error_code, attempts)

    def _ms_since(self, t: float) -> int:
        return int((self._clock() - t) * 1000)

    def _degrade(self, task: str, error_code: str, attempts: list[Attempt]) -> AIUnavailable:
        # 사용자 입력은 남기지 않고 공급자·결과·지연만 기록한다(NFR-005)
        logger.warning(
            "AI 강등 task=%s error_code=%s attempts=%s",
            task,
            error_code,
            [(a.provider, a.outcome, a.latency_ms) for a in attempts],
        )
        return AIUnavailable(task, error_code, tuple(attempts))


def _as_provider_error(exc: BaseException) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return ProviderError(TIMEOUT)
    return ProviderError(UPSTREAM, type(exc).__name__)  # 공급자 구현이 바꾸지 못한 예외도 강등

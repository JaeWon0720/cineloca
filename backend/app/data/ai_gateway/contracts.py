"""AI Gateway 인터페이스와 값 객체 (SDD 3.4 LLMClient·Embedder)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass(frozen=True)
class Prompt:
    """공급자에 보낼 요청. json_schema는 Ollama format·Gemini responseSchema에 그대로 넘긴다."""

    task: str
    system: str
    user: str
    json_schema: dict[str, Any]
    temperature: float
    version: str


@dataclass(frozen=True)
class Attempt:
    """공급자 시도 기록.

    outcome: ok·timeout·rate_limited·upstream·schema·circuit_open·skipped_quota·deadline
    """

    provider: str
    outcome: str
    latency_ms: int


@dataclass(frozen=True)
class LLMResult:
    task: str
    data: BaseModel  # 작업별 출력 모델(스키마 검증 완료)
    provider: str
    latency_ms: int
    attempts: tuple[Attempt, ...]


@runtime_checkable
class MaskedInput(Protocol):
    """마스킹을 거친 사용자 문장. COMP-10의 MaskedText가 이 모양이다(AI-REQ-03).

    데이터 접근 계층이 공통 지원 계층을 import하지 않도록(ADR-002) 모양만 정의한다.
    """

    text: str
    kinds: tuple[str, ...]


class LLMProvider(Protocol):
    """AI 공급자 Strategy(ADR-004). 실패는 ProviderError로 알린다."""

    name: str

    async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
        """모델 출력 원문(JSON 문자열)을 돌려준다."""
        ...


class LLMClient(Protocol):
    """업무 서비스가 쓰는 유일한 AI 인터페이스."""

    async def generate(self, task: str, inputs: Mapping[str, Any], deadline_s: float) -> LLMResult:
        """성공하면 LLMResult, 강등해야 하면 AIUnavailable."""
        ...


class Embedder(Protocol):
    """임베딩(bge-m3, 1024차원, FR-617). 구현은 스프린트 2."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

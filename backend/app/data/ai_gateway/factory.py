"""환경변수로 공급자를 골라 LLMClient·Embedder를 만든다 (SDD 2.4, NFR-009).

AI_PROVIDER
  ollama : 로컬 Ollama 주 + Gemini 보조(키가 있을 때). 팀 서버(시연·배치).
  gemini : Gemini만. 팀원 PC(GPU 없음) 개발용 기본값.
  none   : 공급자 없음. 모든 AI 요청이 강등된다(강등 화면 확인용).
모델 이름은 SDD 표 기본값을 쓰고 환경변수로 바꿀 수 있다(바꾸면 평가셋 재실행, AI-REQ-14).

API 서버·배치는 호출 수와 캐시를 DB에 둔다:
    build_llm_client(settings, usage=PgUsageCounter(), cache=PgAICache())
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.data.ai_gateway.cache import ResponseCache
from app.data.ai_gateway.chain import ProviderChain
from app.data.ai_gateway.contracts import LLMProvider
from app.data.ai_gateway.prompts import DefaultPromptBuilder
from app.data.ai_gateway.providers import GeminiProvider, OllamaEmbedder, OllamaProvider
from app.data.ai_gateway.quota import DailyQuota, UsageCounter
from app.data.ai_gateway.transport import JsonPost

PROVIDER_MODES = ("ollama", "gemini", "none")


@dataclass(frozen=True)
class AISettings:
    provider: str = "gemini"
    ollama_base_url: str | None = None
    ollama_model: str = "qwen3:8b"
    ollama_embed_model: str = "bge-m3"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"  # 착수 PoC p5에서 쓴 무료 모델(TBD-05)
    # 무료 등급 일일 요청 한도. 기본값은 PoC p5에서 본 값, AI Studio에서 확인해 바꾼다(TBD-05)
    gemini_daily_limit: int = 20

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_MODES:
            raise ValueError(f"AI_PROVIDER는 {'·'.join(PROVIDER_MODES)} 중 하나: {self.provider!r}")
        if self.gemini_daily_limit < 0:
            raise ValueError("GEMINI_DAILY_LIMIT는 0 이상")

    def __repr__(self) -> str:  # 키가 로그·오류 출력에 찍히지 않게
        key = "설정됨" if self.gemini_api_key else "없음"
        return (
            f"AISettings(provider={self.provider!r}, ollama_base_url={self.ollama_base_url!r}, "
            f"ollama_model={self.ollama_model!r}, gemini_model={self.gemini_model!r}, "
            f"gemini_daily_limit={self.gemini_daily_limit}, gemini_api_key={key})"
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AISettings:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = (env.get(name) or "").strip()
            return value or None

        return cls(
            provider=(get("AI_PROVIDER") or "gemini").lower(),
            ollama_base_url=get("OLLAMA_BASE_URL"),
            ollama_model=get("OLLAMA_MODEL") or cls.ollama_model,
            ollama_embed_model=get("OLLAMA_EMBED_MODEL") or cls.ollama_embed_model,
            gemini_api_key=get("GEMINI_API_KEY"),
            gemini_model=get("GEMINI_MODEL") or cls.gemini_model,
            gemini_daily_limit=int(get("GEMINI_DAILY_LIMIT") or cls.gemini_daily_limit),
        )


def build_providers(
    settings: AISettings, *, post: JsonPost | None = None
) -> dict[str, LLMProvider]:
    providers: dict[str, LLMProvider] = {}
    if settings.provider == "ollama":
        if not settings.ollama_base_url:
            raise ValueError("AI_PROVIDER=ollama에는 OLLAMA_BASE_URL이 필요합니다")
        providers["ollama"] = OllamaProvider(
            settings.ollama_base_url, settings.ollama_model, post=post
        )
    if settings.provider in ("ollama", "gemini") and settings.gemini_api_key:
        providers["gemini"] = GeminiProvider(
            settings.gemini_api_key, settings.gemini_model, post=post
        )
    return providers


def build_llm_client(
    settings: AISettings,
    *,
    post: JsonPost | None = None,
    usage: UsageCounter | None = None,
    cache: ResponseCache | None = None,
) -> ProviderChain:
    """작업별 순서(로컬 → Gemini)는 TASKS가 정하고, 이 환경에 없는 공급자는 건너뛴다.

    usage가 있으면 공급자별 일일 호출 수를 세고 Gemini 한도를 지킨다(NFR-018).
    cache가 있으면 사용자 화면 작업의 응답을 24시간 재사용한다(AI-REQ-15).
    """
    quota = None
    if usage is not None:
        quota = DailyQuota(usage, {"gemini": settings.gemini_daily_limit})
    return ProviderChain(
        build_providers(settings, post=post), DefaultPromptBuilder(), quota=quota, cache=cache
    )


def build_embedder(settings: AISettings, *, post: JsonPost | None = None) -> OllamaEmbedder | None:
    """임베딩은 로컬 Ollama만 쓴다. Ollama가 없는 환경(팀원 PC)은 None(시드의 벡터를 쓴다)."""
    if not settings.ollama_base_url:
        return None
    return OllamaEmbedder(settings.ollama_base_url, settings.ollama_embed_model, post=post)

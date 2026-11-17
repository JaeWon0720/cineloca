"""AI 일일 호출 한도와 응답 캐시 테스트 (NFR-005·018, AI-REQ-15)."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from app.data.ai_gateway import (
    TASKS,
    AISettings,
    AIUnavailable,
    DailyQuota,
    DefaultPromptBuilder,
    ProviderChain,
    ProviderError,
    build_llm_client,
    cache_key,
)
from app.data.ai_gateway.testing import FakeProvider
from app.support.privacy import mask

GOOD = {
    "out_of_scope": False,
    "work_titles": [],
    "persons": [],
    "genres": [],
    "districts": ["해운대구"],
    "release_year_from": None,
    "release_year_to": None,
    "anchor": None,
    "travel": None,
    "result_type": "location",
}
DAY = date(2026, 11, 17)


class MemoryCounter:
    """usage_counter 가짜. PgUsageCounter.reserve와 같은 규칙."""

    def __init__(self) -> None:
        self.counts: dict[tuple[date, str], int] = {}

    async def reserve(self, day: date, key: str, limit: int | None) -> int | None:
        now = self.counts.get((day, key), 0)
        if limit is not None and now >= limit:
            return None
        self.counts[(day, key)] = now + 1
        return now + 1


class BrokenStore:
    async def reserve(self, day, key, limit):
        raise ConnectionError("postgresql://user:pw@db/x")

    async def get(self, key):
        raise ConnectionError("postgresql://user:pw@db/x")

    async def put(self, key, task, response, ttl_s):
        raise ConnectionError("postgresql://user:pw@db/x")


class MemoryCache:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict[str, Any], int]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        row = self.rows.get(key)
        return None if row is None else row[1]

    async def put(self, key: str, task: str, response: dict[str, Any], ttl_s: int) -> None:
        self.rows[key] = (task, response, ttl_s)


async def _no_sleep(_s: float) -> None:
    return None


def chain(*providers: FakeProvider, quota=None, cache=None) -> ProviderChain:
    return ProviderChain(
        {p.name: p for p in providers},
        DefaultPromptBuilder(),
        quota=quota,
        cache=cache,
        sleep=_no_sleep,
    )


def ask(client, text: str = "해운대 촬영지"):
    return asyncio.run(client.generate("extract_query", {"query": mask(text)}, 20))


def quota(counter, limit: int | None = 2) -> DailyQuota:
    return DailyQuota(counter, {"gemini": limit}, today=lambda: DAY)


# ── 일일 한도 ──────────────────────────────────────────────
def test_counts_every_real_call_per_provider() -> None:
    counter = MemoryCounter()
    ollama = FakeProvider("ollama", [ProviderError("upstream")])
    gemini = FakeProvider("gemini", [{"wrong": 1}, GOOD])  # 스키마 위반 1회 → 재요청

    result = ask(chain(ollama, gemini, quota=quota(counter, 5)))

    assert result.provider == "gemini"
    assert counter.counts == {(DAY, "ollama"): 1, (DAY, "gemini"): 2}  # 재요청도 한도에 든다


def test_gemini_skipped_at_limit_then_degrades() -> None:
    counter = MemoryCounter()
    counter.counts[(DAY, "gemini")] = 2
    gemini = FakeProvider("gemini", [GOOD])

    with pytest.raises(AIUnavailable) as e:
        ask(chain(FakeProvider("ollama", [ProviderError("timeout")]), gemini, quota=quota(counter)))

    assert [(a.provider, a.outcome) for a in e.value.attempts] == [
        ("ollama", "timeout"),
        ("gemini", "skipped_quota"),
    ]
    assert gemini.calls == []


def test_limit_reached_during_schema_retry_moves_on() -> None:
    counter = MemoryCounter()
    gemini = FakeProvider("gemini", [{"wrong": 1}, GOOD])

    with pytest.raises(AIUnavailable) as e:
        ask(chain(gemini, quota=quota(counter, 1)))

    assert [a.outcome for a in e.value.attempts] == ["schema", "skipped_quota"]
    assert len(gemini.calls) == 1


def test_ollama_has_no_limit() -> None:
    counter = MemoryCounter()
    counter.counts[(DAY, "ollama")] = 10_000

    assert ask(chain(FakeProvider("ollama", [GOOD]), quota=quota(counter))).provider == "ollama"


def test_warns_once_at_70_percent_and_at_limit(caplog: pytest.LogCaptureFixture) -> None:
    counter = MemoryCounter()
    q = quota(counter, 10)
    caplog.set_level(logging.WARNING, logger="app.data.ai_gateway.quota")

    results = [asyncio.run(q.reserve("gemini")) for _ in range(12)]

    assert results == [True] * 10 + [False, False]
    messages = [r.getMessage() for r in caplog.records]
    assert sum("70%" in m for m in messages) == 1  # 7번째 호출에서 한 번만
    assert sum("100%" in m for m in messages) == 1


def test_zero_limit_disables_gemini() -> None:
    counter = MemoryCounter()

    assert asyncio.run(quota(counter, 0).reserve("gemini")) is False


def test_counter_failure_does_not_block_ai(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    result = ask(chain(FakeProvider("gemini", [GOOD]), quota=quota(BrokenStore())))

    assert result.provider == "gemini"
    assert "pw@" not in caplog.text  # 접속 정보는 남기지 않는다


# ── 응답 캐시 ──────────────────────────────────────────────
def test_cache_hit_skips_providers_and_counts_nothing() -> None:
    cache, counter = MemoryCache(), MemoryCounter()
    gemini = FakeProvider("gemini", [GOOD])
    client = chain(gemini, quota=quota(counter, 5), cache=cache)

    first = ask(client)
    second = ask(client)

    assert first.provider == "gemini"
    assert (second.provider, second.attempts) == ("cache", ())  # 외부 호출 0회
    assert second.data == first.data
    assert len(gemini.calls) == 1
    assert counter.counts == {(DAY, "gemini"): 1}
    ((task, stored, ttl),) = cache.rows.values()
    assert (task, ttl) == ("extract_query", 24 * 60 * 60)
    assert stored == GOOD


def test_cache_key_depends_on_masked_text_and_version() -> None:
    spec = TASKS["extract_query"]
    a = cache_key(spec, {"query": mask("연락처 010-1234-5678 해운대")})
    b = cache_key(spec, {"query": mask("연락처 010-9999-8888 해운대")})
    c = cache_key(spec, {"query": mask("광안리")})

    assert a == b  # 가린 뒤 같은 문장이면 같은 키(원문은 키에도 들어가지 않음)
    assert a != c and len(a) == 64
    v4 = type(spec)(**{**spec.__dict__, "prompt_version": "v4"})
    assert cache_key(v4, {"query": mask("광안리")}) != c


def test_cache_key_handles_dataclass_inputs() -> None:
    @dataclass(frozen=True)
    class Evidence:
        work_id: int
        facts: tuple[str, ...]

    spec = TASKS["work_intro"]
    one = cache_key(spec, {"evidence": Evidence(1, ("부산 촬영",))})

    assert one == cache_key(spec, {"evidence": Evidence(1, ("부산 촬영",))})
    assert one != cache_key(spec, {"evidence": Evidence(2, ("부산 촬영",))})


def test_failed_or_degraded_answers_are_not_cached() -> None:
    cache = MemoryCache()

    with pytest.raises(AIUnavailable):
        ask(chain(FakeProvider("gemini", [{"wrong": 1}, {"wrong": 2}]), cache=cache))
    assert cache.rows == {}


def test_batch_tasks_are_not_cached() -> None:
    assert TASKS["suggest_tags"].cache_ttl_s is None
    assert TASKS["judge_match"].cache_ttl_s is None
    assert TASKS["extract_query"].cache_ttl_s == 24 * 60 * 60


def test_broken_or_stale_cache_is_a_miss() -> None:
    gemini = FakeProvider("gemini", [GOOD, GOOD])
    assert ask(chain(gemini, cache=BrokenStore())).provider == "gemini"

    stale = MemoryCache()
    key = cache_key(TASKS["extract_query"], {"query": mask("해운대 촬영지")})
    stale.rows[key] = ("extract_query", {"old": "형식"}, 1)
    assert ask(chain(gemini, cache=stale)).provider == "gemini"
    assert json.dumps(stale.rows[key][1], ensure_ascii=False) == json.dumps(
        GOOD, ensure_ascii=False
    )


# ── 조립 ──────────────────────────────────────────────────
def test_factory_wires_gemini_limit_from_env() -> None:
    settings = AISettings.from_env(
        {"AI_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key", "GEMINI_DAILY_LIMIT": "3"}
    )
    assert settings.gemini_daily_limit == 3
    assert AISettings.from_env({}).gemini_daily_limit == 20
    with pytest.raises(ValueError):
        AISettings(gemini_daily_limit=-1)

    client = build_llm_client(settings, usage=MemoryCounter(), cache=MemoryCache())
    assert client._quota is not None and client._cache is not None  # noqa: SLF001
    assert build_llm_client(settings)._quota is None  # noqa: SLF001

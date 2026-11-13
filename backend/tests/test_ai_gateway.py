"""COMP-08 AI Gateway 초안 테스트 (AI-REQ-02·03·04, NFR-002).

실제 AI 없이 가짜 공급자로 시험한다.
"""

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from app.data.ai_gateway import (
    AIUnavailable,
    DefaultPromptBuilder,
    Prompt,
    ProviderChain,
    ProviderError,
    TaskSpec,
)
from app.data.ai_gateway.circuit import CircuitBreaker
from app.data.ai_gateway.tasks import TASKS, QueryConditions
from app.data.ai_gateway.testing import FakeProvider
from app.support.privacy import mask

GOOD = {
    "out_of_scope": False,
    "work_titles": ["부산행"],
    "persons": [],
    "genres": ["액션"],
    "districts": ["해운대구"],
    "release_year_from": None,
    "release_year_to": None,
    "anchor": {"type": "station", "name": None},
    "travel": {"mode": "walk", "max_min": 10},
    "result_type": "location",
}
BAD_DISTRICT = {**GOOD, "districts": ["광안리"]}  # 허용 값 밖 → 스키마 위반


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    sleeps: list[float]


class EchoPrompts:
    """프롬프트 내용과 상관없이 쓰는 간단한 빌더."""

    def build(self, spec: TaskSpec, inputs: Mapping[str, Any]) -> Prompt:
        return Prompt(spec.name, "system", "user", {}, spec.temperature, spec.prompt_version)


def make_chain(*providers: FakeProvider, **kwargs: Any) -> tuple[ProviderChain, FakeClock]:
    clock = FakeClock()
    clock.sleeps = []
    chain = ProviderChain(
        {p.name: p for p in providers},
        EchoPrompts(),
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )
    return chain, clock


def run(chain: ProviderChain, task: str = "extract_query", deadline_s: float = 8.0):
    return asyncio.run(chain.generate(task, {"query": mask("부산행 촬영지")}, deadline_s))


def outcomes(attempts) -> list[tuple[str, str]]:
    return [(a.provider, a.outcome) for a in attempts]


def test_first_provider_success() -> None:
    ollama = FakeProvider("ollama", [GOOD])
    chain, _ = make_chain(ollama, FakeProvider("gemini", []))

    result = run(chain)

    assert result.provider == "ollama"
    assert isinstance(result.data, QueryConditions)
    assert result.data.districts == ["해운대구"]
    assert outcomes(result.attempts) == [("ollama", "ok")]


def test_falls_back_to_next_provider_on_timeout() -> None:
    chain, _ = make_chain(
        FakeProvider("ollama", [ProviderError("timeout")]), FakeProvider("gemini", [GOOD])
    )

    result = run(chain)

    assert result.provider == "gemini"
    assert outcomes(result.attempts) == [("ollama", "timeout"), ("gemini", "ok")]


def test_unexpected_provider_exception_is_degraded_not_raised() -> None:
    chain, _ = make_chain(FakeProvider("ollama", [RuntimeError("boom")]))

    with pytest.raises(AIUnavailable) as err:
        run(chain)

    assert outcomes(err.value.attempts) == [("ollama", "upstream")]


def test_schema_violation_retries_same_provider_once() -> None:
    ollama = FakeProvider("ollama", [BAD_DISTRICT, GOOD])
    chain, _ = make_chain(ollama, FakeProvider("gemini", []))

    result = run(chain)

    assert result.provider == "ollama"
    assert len(ollama.calls) == 2
    assert outcomes(result.attempts) == [("ollama", "schema"), ("ollama", "ok")]


def test_schema_violation_twice_moves_to_next_provider() -> None:
    chain, _ = make_chain(
        FakeProvider("ollama", ["not json", BAD_DISTRICT]), FakeProvider("gemini", [GOOD])
    )

    result = run(chain)

    assert result.provider == "gemini"
    assert outcomes(result.attempts) == [
        ("ollama", "schema"),
        ("ollama", "schema"),
        ("gemini", "ok"),
    ]


def test_schema_failure_everywhere_degrades_with_ai_schema() -> None:
    chain, _ = make_chain(
        FakeProvider("ollama", [BAD_DISTRICT, BAD_DISTRICT]),
        FakeProvider("gemini", [BAD_DISTRICT, BAD_DISTRICT]),
    )

    with pytest.raises(AIUnavailable) as err:
        run(chain)

    assert err.value.error_code == "AI_SCHEMA"
    assert len(err.value.attempts) == 4


def test_all_providers_down_degrades_with_ai_unavailable() -> None:
    chain, _ = make_chain(
        FakeProvider("ollama", [ProviderError("upstream")]),
        FakeProvider("gemini", [ProviderError("timeout")]),
    )

    with pytest.raises(AIUnavailable) as err:
        run(chain)

    assert err.value.error_code == "AI_UNAVAILABLE"
    assert outcomes(err.value.attempts) == [("ollama", "upstream"), ("gemini", "timeout")]


def test_rate_limited_short_retry_after_retries_same_provider() -> None:
    chain, clock = make_chain(
        FakeProvider("ollama", []),
        FakeProvider("gemini", [ProviderError("rate_limited", retry_after_s=1.5), GOOD]),
        tasks={"extract_query": TaskSpec("extract_query", QueryConditions, providers=("gemini",))},
    )

    result = run(chain)

    assert result.provider == "gemini"
    assert clock.sleeps == [1.5]
    assert outcomes(result.attempts) == [("gemini", "rate_limited"), ("gemini", "ok")]


def test_rate_limited_long_retry_after_moves_on_without_waiting() -> None:
    chain, clock = make_chain(
        FakeProvider("ollama", [ProviderError("rate_limited", retry_after_s=30)]),
        FakeProvider("gemini", [GOOD]),
    )

    result = run(chain)

    assert result.provider == "gemini"
    assert clock.sleeps == []


def test_circuit_opens_after_five_failures_and_recovers_after_60s() -> None:
    ollama = FakeProvider("ollama", [ProviderError("upstream")] * 5 + [GOOD])
    gemini = FakeProvider("gemini", [GOOD] * 6)
    chain, clock = make_chain(ollama, gemini)

    for _ in range(5):
        assert run(chain).provider == "gemini"
    assert chain.breaker("ollama").is_open

    result = run(chain)  # 차단기 열림: ollama를 부르지 않음
    assert outcomes(result.attempts) == [("ollama", "circuit_open"), ("gemini", "ok")]
    assert len(ollama.calls) == 5

    clock.now += 60
    assert run(chain).provider == "ollama"  # 60초 뒤 다시 시도


def test_schema_failure_does_not_open_circuit() -> None:
    ollama = FakeProvider("ollama", [BAD_DISTRICT] * 10)
    chain, _ = make_chain(ollama, FakeProvider("gemini", [GOOD] * 5))

    for _ in range(5):
        run(chain)

    assert not chain.breaker("ollama").is_open


def test_deadline_shortage_degrades_immediately() -> None:
    class SlowFail(FakeProvider):
        async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
            clock.now += 7.5  # 7.5초 쓰고 실패
            raise ProviderError("timeout")

    chain, clock = make_chain(SlowFail("ollama", []), FakeProvider("gemini", [GOOD]))

    with pytest.raises(AIUnavailable) as err:
        run(chain, deadline_s=8.0)  # 남은 0.5초 < 최소 1초

    assert err.value.error_code == "AI_DEADLINE"
    assert outcomes(err.value.attempts) == [("ollama", "timeout"), ("gemini", "deadline")]


def test_provider_timeout_is_limited_by_remaining_deadline() -> None:
    seen: list[float] = []

    class Recorder(FakeProvider):
        async def complete(self, prompt: Prompt, *, timeout_s: float) -> str:
            seen.append(timeout_s)
            return json.dumps(GOOD)

    chain, _ = make_chain(Recorder("ollama", []))
    run(chain, deadline_s=4.0)

    assert seen == [4.0]  # 작업 제한 10초보다 남은 마감 4초가 우선


def test_quota_exhausted_provider_is_skipped() -> None:
    gemini = FakeProvider("gemini", [GOOD])
    chain, _ = make_chain(
        FakeProvider("ollama", [ProviderError("upstream")]),
        gemini,
        allow_provider=lambda name: name != "gemini",
    )

    with pytest.raises(AIUnavailable) as err:
        run(chain)

    assert outcomes(err.value.attempts) == [("ollama", "upstream"), ("gemini", "skipped_quota")]
    assert gemini.calls == []


def test_missing_provider_is_ignored_on_dev_pc() -> None:
    chain, _ = make_chain(FakeProvider("gemini", [GOOD]))  # 팀원 PC: Ollama 없음

    assert run(chain).provider == "gemini"


def test_code_fenced_json_is_accepted() -> None:
    fenced = "```json\n" + json.dumps(GOOD, ensure_ascii=False) + "\n```"
    chain, _ = make_chain(FakeProvider("ollama", [fenced]))

    assert run(chain).provider == "ollama"


def test_user_text_must_be_masked() -> None:
    chain, _ = make_chain(FakeProvider("ollama", [GOOD]))

    with pytest.raises(TypeError):
        asyncio.run(chain.generate("extract_query", {"query": "010-1234-5678 부산행"}, 8.0))


def test_unknown_task_is_programming_error() -> None:
    chain, _ = make_chain(FakeProvider("ollama", [GOOD]))

    with pytest.raises(ValueError):
        asyncio.run(chain.generate("no_such_task", {}, 8.0))


def test_default_prompt_uses_masked_text_and_allowed_values() -> None:
    prompt = DefaultPromptBuilder().build(
        TASKS["extract_query"], {"query": mask("010-1234-5678 마동석 영화 촬영지")}
    )

    assert "010-1234-5678" not in prompt.user
    assert "[개인정보] 마동석 영화 촬영지" in prompt.user
    assert "해운대구" in prompt.system and "멜로/로맨스" in prompt.system
    assert prompt.version == "v2"
    assert prompt.temperature == 0.0
    assert prompt.json_schema["title"] == "QueryConditions"


def test_other_task_prompts_are_not_written_yet() -> None:
    with pytest.raises(NotImplementedError):
        DefaultPromptBuilder().build(TASKS["work_intro"], {})


def test_registered_tasks_match_sdd_table() -> None:
    assert set(TASKS) == {
        "extract_query",
        "summarize_results",
        "work_intro",
        "course_intro",
        "suggest_tags",
        "judge_match",
    }
    assert all(0.0 <= spec.temperature <= 0.3 for spec in TASKS.values())
    assert all(spec.providers == ("ollama", "gemini") for spec in TASKS.values())


def test_circuit_breaker_unit() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, open_seconds=10, clock=clock)

    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()
    clock.now += 10
    assert breaker.allow()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.allow()  # 성공하면 연속 실패 수가 초기화됨

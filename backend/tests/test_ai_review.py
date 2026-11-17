"""C1 AI 태그·매칭 제안 테스트 (FR-608, BR-04, AI-REQ-04·13).

실제 AI 없이 가짜 공급자와 실제 프롬프트 빌더로 시험한다.
"""

import asyncio
from typing import get_args

import pytest
from pydantic import ValidationError

from app.batch.ai_review import judge_matches, suggest_location_tags
from app.core.place_tags import PLACE_TYPES, PlaceType
from app.data.ai_gateway import DefaultPromptBuilder, ProviderChain
from app.data.ai_gateway.tasks import (
    TASKS,
    MatchCandidate,
    MatchInput,
    MatchJudgement,
    TagInput,
    TagSuggestion,
)
from app.data.ai_gateway.testing import FakeProvider

BEACH = TagInput(7, "송정해수욕장", "부산 해운대구 송정해변로 62", "해운대구")
SHOP = TagInput(8, "현전사", None, None)

CASE = MatchInput(
    work_id=31,
    title="친구",
    year=2001,
    places=("범일동 철길", "자갈치시장"),
    candidates=(
        MatchCandidate("K01", "친구", 2001, "극영화", "곽경택", busan_evidence=True),
        MatchCandidate("K02", "친구", 1985, "극영화", "다른 감독"),
    ),
)


async def _no_sleep(_seconds: float) -> None:
    return None


def chain(*providers: FakeProvider) -> ProviderChain:
    return ProviderChain({p.name: p for p in providers}, DefaultPromptBuilder(), sleep=_no_sleep)


def tags(*providers: FakeProvider, places=(BEACH,)):
    return asyncio.run(suggest_location_tags(chain(*providers), places))


def judge(*providers: FakeProvider, cases=(CASE,)):
    return asyncio.run(judge_matches(chain(*providers), cases))


# ── 태그 목록 ──────────────────────────────────────────────
def test_place_types_match_literal_and_hints() -> None:
    hints = [w for words in PLACE_TYPES.values() for w in words]

    assert list(PLACE_TYPES) == list(get_args(PlaceType))
    assert all(PLACE_TYPES.values())
    assert len(hints) == len(set(hints))  # 같은 예시 말이 두 유형에 있으면 규칙이 헷갈린다


# ── 출력 스키마 ────────────────────────────────────────────
def test_tag_suggestion_only_allows_listed_types_up_to_two() -> None:
    TagSuggestion(tags=["해변"], reason="이름에 해수욕장")
    TagSuggestion(tags=[], reason="이름만으로 업종을 알 수 없음")
    with pytest.raises(ValidationError):
        TagSuggestion(tags=["야경"], reason="목록에 없는 태그")
    with pytest.raises(ValidationError):
        TagSuggestion(tags=["해변", "공원·산", "다리"], reason="3개")


@pytest.mark.parametrize(
    ("verdict", "candidate", "ok"),
    [
        ("match", 1, True),
        ("not_listed", None, True),
        ("unsure", None, True),
        ("match", None, False),  # match인데 후보 번호 없음
        ("unsure", 2, False),  # match가 아닌데 후보 번호 있음
        ("match", 0, False),  # 번호는 1부터
        ("same", 1, False),
    ],
)
def test_match_judgement_rules(verdict: str, candidate: int | None, ok: bool) -> None:
    data = {"verdict": verdict, "candidate": candidate, "reason": "근거"}
    if ok:
        MatchJudgement.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            MatchJudgement.model_validate(data)


# ── 프롬프트 ───────────────────────────────────────────────
def test_tag_prompt_lists_types_and_place() -> None:
    prompt = DefaultPromptBuilder().build(TASKS["suggest_tags"], {"place": SHOP})

    assert "해변(해수욕장, 해변, 백사장)" in prompt.system
    assert "추측하지 말고" in prompt.system
    assert prompt.user == "장소: 현전사\n주소: 없음\n구·군: 없음"
    assert (prompt.version, prompt.temperature) == ("v1", 0.0)
    assert prompt.json_schema["title"] == "TagSuggestion"


def test_match_prompt_numbers_candidates() -> None:
    prompt = DefaultPromptBuilder().build(TASKS["judge_match"], {"case": CASE})

    assert "작품(촬영지 데이터): 친구" in prompt.user
    assert "이 작품의 촬영지: 범일동 철길, 자갈치시장" in prompt.user
    assert "1. 친구 / 연도 2001 / 유형 극영화 / 감독 곽경택 / 부산 촬영 근거 있음" in prompt.user
    assert "2. 친구 / 연도 1985 / 유형 극영화 / 감독 다른 감독 / 부산 촬영 근거 없음" in prompt.user
    assert "KMDb" in prompt.system and prompt.version == "v1"


# ── 태그 제안 ──────────────────────────────────────────────
def test_tag_suggestion_goes_to_review_queue_as_ai_proposal() -> None:
    ollama = FakeProvider("ollama", [{"tags": ["해변", "해변"], "reason": "이름에 해수욕장"}])

    (item,), stats = tags(ollama)

    assert (item.kind, item.target_type, item.target_id) == ("tag", "location", 7)
    assert (item.proposer, item.score) == ("ai", None)  # BR-04: 사람이 승인하기 전 제안일 뿐
    assert item.proposal == {
        "tags": ["해변"],  # 중복은 하나로
        "reason": "이름에 해수욕장",
        "provider": "ollama",
        "prompt_version": "v1",
    }
    assert (stats.requested, stats.proposed, stats.failed) == (1, 1, 0)
    assert stats.providers == {"ollama": 1}


def test_unknown_place_gets_empty_suggestion() -> None:
    ollama = FakeProvider("ollama", [{"tags": [], "reason": "이름만으로 업종을 알 수 없음"}])

    (item,), _ = tags(ollama, places=(SHOP,))

    assert item.proposal["tags"] == []
    assert "error" not in item.proposal


def test_tag_outside_list_retries_then_falls_back_to_gemini() -> None:
    ollama = FakeProvider("ollama", [{"tags": ["야경"], "reason": "x"}] * 2)
    gemini = FakeProvider("gemini", [{"tags": ["해변"], "reason": "이름에 해수욕장"}])

    (item,), stats = tags(ollama, gemini)

    assert len(ollama.calls) == 2  # 스키마 위반 → 같은 공급자 1회 재요청(AI-REQ-04)
    assert item.proposal["provider"] == "gemini"
    assert stats.providers == {"gemini": 1}


def test_ai_down_still_queues_place_without_suggestion() -> None:
    (item,), stats = tags(FakeProvider("ollama", []), FakeProvider("gemini", []))

    assert item.proposal == {"tags": [], "reason": None, "error": "AI_UNAVAILABLE"}
    assert (stats.proposed, stats.failed) == (1, 1)
    assert stats.errors == {"AI_UNAVAILABLE": 1}


def test_places_are_asked_one_by_one() -> None:
    ollama = FakeProvider(
        "ollama",
        [{"tags": ["해변"], "reason": "해수욕장"}, {"tags": [], "reason": "알 수 없음"}],
    )

    items, stats = tags(ollama, places=(BEACH, SHOP))

    assert [i.target_id for i in items] == [7, 8]
    assert ["송정해수욕장" in c.user for c in ollama.calls] == [True, False]
    assert stats.requested == 2


# ── 매칭 판정 ──────────────────────────────────────────────
def test_match_judgement_records_chosen_candidate() -> None:
    ollama = FakeProvider(
        "ollama", [{"verdict": "match", "candidate": 1, "reason": "제목·연도·부산 근거 일치"}]
    )

    (item,), stats = judge(ollama)

    assert (item.kind, item.target_type, item.target_id, item.proposer) == (
        "match",
        "work",
        31,
        "ai",
    )
    assert item.proposal == {
        "verdict": "match",
        "candidate": {"docid": "K01", "title": "친구", "year": 2001},
        "reason": "제목·연도·부산 근거 일치",
        "provider": "ollama",
        "prompt_version": "v1",
    }
    assert (stats.requested, stats.proposed) == (1, 1)


def test_not_listed_has_no_candidate() -> None:
    ollama = FakeProvider(
        "ollama", [{"verdict": "not_listed", "candidate": None, "reason": "방송 표기"}]
    )

    (item,), _ = judge(ollama)

    assert (item.proposal["verdict"], item.proposal["candidate"]) == ("not_listed", None)


def test_candidate_number_out_of_range_is_dropped() -> None:
    ollama = FakeProvider("ollama", [{"verdict": "match", "candidate": 3, "reason": "x"}])

    items, stats = judge(ollama)

    assert items == []
    assert (stats.requested, stats.proposed, stats.invalid) == (1, 0, 1)


def test_match_ai_down_adds_nothing() -> None:
    items, stats = judge(FakeProvider("ollama", []), FakeProvider("gemini", []))

    assert items == []  # 규칙이 만든 검수 항목이 이미 있다
    assert (stats.failed, stats.errors) == (1, {"AI_UNAVAILABLE": 1})


def test_case_without_candidates_is_not_sent() -> None:
    ollama = FakeProvider("ollama", [])
    empty = MatchInput(work_id=40, title="광고 A", year=None, candidates=())

    items, stats = judge(ollama, cases=(empty,))

    assert items == [] and ollama.calls == []
    assert stats.requested == 0

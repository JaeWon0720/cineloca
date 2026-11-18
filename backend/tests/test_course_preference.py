"""F3 취향 구조화·후보 점수화 테스트 (FR-307~309·312, AI-REQ-04)."""

import asyncio

import pytest
from pydantic import ValidationError

from app.core.course_scoring import TOP_K, WEIGHTS
from app.data.ai_gateway import TASKS, DefaultPromptBuilder, ProviderChain, ProviderError
from app.data.ai_gateway.tasks import Anchor, Preference
from app.data.ai_gateway.testing import FakeProvider
from app.data.course_candidates import CandidateLocation, parse_vector
from app.services.course_preference import (
    CandidateScorer,
    PreferenceInputError,
    PreferenceService,
    apply_selection,
    preference_from_selection,
    preference_vector,
)
from app.support import request_context
from app.support.privacy import mask

SEA = {
    "genres": [],
    "tags": ["해변", "골목·주택가"],
    "work_titles": [],
    "persons": [],
    "districts": [],
    "start": {"type": "station", "name": "서면"},
    "time_budget_h": 4,
    "avoid_tags": ["시장"],
}

SEOMYEON = (35.1578, 129.0592)
BEACH = CandidateLocation(
    1, "광안리해수욕장", "수영구", 35.1532, 129.1186, ("해변",), ("드라마",), ("해운대",), (), 3,
    (1.0, 0.0),
)  # fmt: skip
ALLEY = CandidateLocation(
    2, "범일동 철길", "동구", 35.1405, 129.0590, ("골목·주택가",), ("느와르",), ("친구",),
    ("곽경택", "유오성"), 6, (0.6, 0.8),
)  # fmt: skip
MARKET = CandidateLocation(
    3, "자갈치시장", "중구", 35.0966, 129.0306, ("시장",), ("드라마",), ("국제시장",), (), 17,
)  # fmt: skip
FAR = CandidateLocation(4, "기장 해변", "기장군", 35.244, 129.222, ("해변",), (), (), (), 1)


async def _no_sleep(_s: float) -> None:
    return None


def service(*replies) -> tuple[PreferenceService, FakeProvider]:
    gemini = FakeProvider("gemini", list(replies))
    chain = ProviderChain({"gemini": gemini}, DefaultPromptBuilder(), sleep=_no_sleep)
    return PreferenceService(chain), gemini


def structure(svc, text="바다 보이는 곳이랑 오래된 골목, 반나절, 서면 출발, 시장은 빼고", **kw):
    return asyncio.run(svc.structure(text, **kw))


# ── 취향 구조화(FR-308) ─────────────────────────────────────
def test_structure_preference_from_sentence() -> None:
    svc, gemini = service(SEA)

    r = structure(svc)

    assert r.status == "ok" and r.provider == "gemini"
    assert r.preference.tags == ["해변", "골목·주택가"]
    assert r.preference.avoid_tags == ["시장"]
    assert r.preference.start == Anchor(type="station", name="서면")
    assert r.preference.time_budget_h == 4
    assert gemini.calls[0].task == "structure_preference"
    assert gemini.calls[0].user.startswith("취향: 바다 보이는 곳")


def test_selected_items_win_over_sentence() -> None:  # FR-308
    svc, _ = service(SEA)

    r = structure(svc, start=Anchor(type="current"), budget_h=6)

    assert r.preference.start == Anchor(type="current")
    assert r.preference.time_budget_h == 6


def test_budget_defaults_to_4_hours() -> None:  # FR-307
    svc, _ = service({**SEA, "time_budget_h": None})

    assert structure(svc).preference.time_budget_h == 4


@pytest.mark.parametrize(
    "reply",
    [
        {**SEA, "tags": ["야경"]},  # 목록에 없는 태그
        {**SEA, "time_budget_h": 12},  # 1~8시간 밖
        {**SEA, "genres": ["애니메이션"]},  # 장르 묶음에 없음
    ],
)
def test_schema_violation_is_rejected(reply: dict) -> None:  # AI-REQ-04
    with pytest.raises(ValidationError):
        Preference.model_validate(reply)


def test_ai_failure_opens_manual_selection() -> None:  # FR-312, UC-09 3a
    svc, gemini = service(ProviderError("timeout"))

    r = structure(svc, budget_h=3)

    assert r.status == "manual"
    assert r.preference == Preference(time_budget_h=3)  # 선택 항목만 미리 채움
    assert "직접 고를 수 있게" in r.notices[-1]


def test_schema_failure_twice_opens_manual_selection() -> None:
    svc, gemini = service({**SEA, "tags": ["야경"]}, {**SEA, "tags": ["야경"]})

    r = structure(svc)

    assert r.status == "manual" and len(gemini.calls) == 2  # 1회 다시 요청 후 강등


def test_only_masked_text_reaches_ai_and_log() -> None:
    svc, gemini = service(SEA)
    ctx, token = request_context.begin("rid")
    try:
        r = structure(svc, text="010-1234-5678로 연락 주세요, 바다 좋아요")
    finally:
        request_context.end(token)

    assert r.masked and "010-1234-5678" not in gemini.calls[0].user
    assert "010-1234-5678" not in (ctx.masked_query or "")
    assert ctx.ai_calls == {"gemini": 1}


@pytest.mark.parametrize(("text", "budget"), [("", None), ("가" * 201, None), ("바다", 9)])
def test_input_validation(text: str, budget: float | None) -> None:  # FR-307
    svc, gemini = service()
    with pytest.raises(PreferenceInputError):
        structure(svc, text=text, budget_h=budget)
    assert gemini.calls == []


def test_manual_selection_builds_same_preference() -> None:  # FR-312
    pref = preference_from_selection(
        genres=["범죄·느와르"], tags=["골목·주택가"], districts=["동구"], budget_h=2
    )

    assert (pref.genres, pref.tags, pref.districts, pref.time_budget_h) == (
        ["범죄·느와르"],
        ["골목·주택가"],
        ["동구"],
        2,
    )
    with pytest.raises(PreferenceInputError):
        preference_from_selection(tags=["야경"])
    with pytest.raises(PreferenceInputError):
        apply_selection(Preference(), budget_h=0.5)


def test_prompt_lists_allowed_values() -> None:
    prompt = DefaultPromptBuilder().build(TASKS["structure_preference"], {"text": mask("바다")})

    assert "해변(해수욕장, 해변, 백사장)" in prompt.system
    assert "범죄·느와르(" in prompt.system and "해운대구" in prompt.system
    assert "'반나절' → 4" in prompt.system
    assert (prompt.version, prompt.temperature) == ("v1", 0.0)


# ── 후보 점수화(FR-309) ─────────────────────────────────────
def test_weights_sum_to_one() -> None:
    assert abs(sum(WEIGHTS.values()) - 1) < 1e-9
    assert TOP_K == 15


def test_score_ranks_by_preference_and_excludes_avoided() -> None:
    pref = Preference.model_validate(SEA)

    ranked = CandidateScorer().score(
        [MARKET, FAR, BEACH, ALLEY], pref, preference_vector=[1.0, 0.0], start_point=SEOMYEON
    )

    names = [s.location.name for s in ranked]
    assert names[-1] == "기장 해변" and set(names) == {"광안리해수욕장", "범일동 철길", "기장 해변"}
    assert "자갈치시장" not in names  # 피할 유형(시장)은 작품이 많아도 뺀다
    beach = next(s for s in ranked if s.location.location_id == 1)
    alley = next(s for s in ranked if s.location.location_id == 2)
    assert beach.matched == ("태그: 해변",) and alley.matched == ("태그: 골목·주택가",)
    assert (beach.parts["match"], beach.parts["similarity"]) == (1.0, 1.0)
    assert alley.parts["similarity"] == pytest.approx(0.6)
    assert alley.parts["distance"] > beach.parts["distance"] > ranked[-1].parts["distance"]
    expected = sum(WEIGHTS[k] * v for k, v in alley.parts.items())
    assert alley.score == pytest.approx(expected)  # 가중합(FR-309)


def test_match_counts_each_asked_facet() -> None:
    pref = Preference(genres=["범죄·느와르"], persons=["곽경택"], districts=["중구"])

    (alley,) = CandidateScorer(top_k=1).score([ALLEY, BEACH], pref)

    assert alley.location.location_id == 2
    assert alley.parts["match"] == pytest.approx(
        2 / 3
    )  # 장르(느와르→범죄·느와르)·인물 맞음, 구 틀림
    assert alley.matched == ("장르: 범죄·느와르", "인물: 곽경택")


def test_without_vector_or_start_those_parts_are_zero() -> None:
    (s,) = CandidateScorer(top_k=1).score([BEACH], Preference(tags=["해변"]))

    assert (s.parts["similarity"], s.parts["distance"]) == (0.0, 0.0)
    assert s.score == pytest.approx(WEIGHTS["match"] + WEIGHTS["works"] * 3 / 5)


def test_top_k_and_custom_weights() -> None:
    many = [
        CandidateLocation(i, f"촬영지{i}", "중구", 35.1, 129.0, work_count=i % 7) for i in range(30)
    ]
    ranked = CandidateScorer({"match": 0, "similarity": 0, "works": 1, "distance": 0}).score(
        many, Preference()
    )

    assert len(ranked) == 15
    assert all(s.parts["works"] == 1.0 for s in ranked[:8])  # 5편 이상은 같은 점수(WORKS_CAP)
    assert min(s.location.work_count for s in ranked) >= 3
    with pytest.raises(ValueError):
        CandidateScorer({"match": 1})


# ── 임베딩 ────────────────────────────────────────────────
class FakeEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.seen: list[str] = []

    async def embed(self, texts):
        self.seen += list(texts)
        if self.fail:
            raise ProviderError("timeout")
        return [[0.1, 0.2]]


def test_preference_vector_uses_masked_text_and_degrades() -> None:
    emb = FakeEmbedder()
    text = mask("010-1234-5678 바다")

    assert asyncio.run(preference_vector(emb, text)) == [0.1, 0.2]
    assert "010-1234-5678" not in emb.seen[0]
    assert asyncio.run(preference_vector(FakeEmbedder(fail=True), text)) is None
    assert asyncio.run(preference_vector(None, text)) is None


def test_parse_vector() -> None:
    assert parse_vector("[0.5,-1,2e-3]") == (0.5, -1.0, 0.002)
    assert parse_vector("{1,2}") == (1.0, 2.0)
    assert parse_vector(None) is None and parse_vector("[]") is None

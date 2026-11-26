"""F1 AI 작품 소개 테스트 (FR-108~111, AI-REQ-01·06·07, AC-04)."""

import asyncio

from app.data.ai_gateway import TASKS, DefaultPromptBuilder, ProviderChain, ProviderError
from app.data.ai_gateway.testing import FakeProvider
from app.services.work_intro import (
    POLICIES,
    LocationFact,
    NoSpoilerEvidence,
    PartialSpoilerEvidence,
    WorkFacts,
    WorkIntroService,
)

WORK = WorkFacts(
    work_id=31,
    title="친구",
    kmdb_matched=True,
    release_year=2001,
    kind="극영화",
    genres=("범죄·느와르",),
    director="곽경택",
    cast=("유오성", "장동건", "서태화", "정운택", "김보경", "기주봉"),
    synopsis="1976년 부산, 네 친구 준석과 동수는 함께 자란다. 결말에서 동수는 죽음을 맞는다.",
    locations=(
        LocationFact("범일동 철길", "동구", "네 친구가 뛰어가는 장면"),
        LocationFact("자갈치시장", "중구"),
    ),
)

GOOD = {
    "sentences": [
        {"text": "친구는 곽경택 감독의 2001년 작품입니다.", "entities": ["친구", "곽경택"]},
        {
            "text": "범일동 철길에서 네 친구가 뛰어가는 장면을 찍었습니다.",
            "entities": ["범일동 철길"],
        },
        {"text": "중구 자갈치시장도 주요 촬영지입니다.", "entities": ["중구", "자갈치시장"]},
    ]
}


async def _no_sleep(_s: float) -> None:
    return None


def service(*replies) -> tuple[WorkIntroService, FakeProvider]:
    gemini = FakeProvider("gemini", list(replies))
    chain = ProviderChain({"gemini": gemini}, DefaultPromptBuilder(), sleep=_no_sleep)
    return WorkIntroService(chain), gemini


def introduce(svc: WorkIntroService, work: WorkFacts = WORK, level: str = "none"):
    return asyncio.run(svc.introduce(work, POLICIES[level]))


# ── 근거 묶음(스포일러 단계) ───────────────────────────────
def test_no_spoiler_evidence_has_no_synopsis() -> None:
    evidence = NoSpoilerEvidence().build(WORK)

    assert not any("줄거리" in f for f in evidence.facts)  # AI-REQ-07
    assert evidence.facts[0] == "작품: 친구 (2001), 극영화"
    assert "출연: 유오성, 장동건, 서태화, 정운택, 김보경" in evidence.facts  # 최대 5명
    assert "촬영지: 범일동 철길 (부산 동구)" in evidence.facts
    assert not any("장면" in f for f in evidence.facts)  # 장면 설명도 줄거리를 담을 수 있어 뺀다
    assert {"친구", "곽경택", "범일동 철길", "동구", "자갈치시장"} <= evidence.names
    assert "기주봉" not in evidence.names


def test_partial_evidence_adds_synopsis() -> None:
    evidence = PartialSpoilerEvidence().build(WORK)

    assert evidence.facts[-1].startswith("줄거리: 1976년 부산")
    assert "촬영지: 범일동 철길 (부산 동구) — 네 친구가 뛰어가는 장면" in evidence.facts


def test_prompt_contains_only_evidence() -> None:
    evidence = NoSpoilerEvidence().build(WORK)

    prompt = DefaultPromptBuilder().build(TASKS["work_intro"], {"evidence": evidence})

    assert prompt.user.splitlines()[0] == "근거:"
    assert "- 촬영지: 자갈치시장 (부산 중구)" in prompt.user
    assert "300~500자" in prompt.system and "결말·반전" in prompt.system
    assert (prompt.version, prompt.temperature) == ("v5", 0.0)


# ── 소개 생성·검증·강등 ────────────────────────────────────
def test_grounded_intro_is_shown_with_ai_label() -> None:
    svc, gemini = service(GOOD)

    result = introduce(svc)

    assert result.status == "ok" and result.ai_generated  # FR-707
    assert len(result.sentences) == 3
    assert result.provider == "gemini"
    assert "줄거리" not in gemini.calls[0].user  # '없음' 단계


def test_hallucinated_sentences_are_dropped() -> None:
    reply = {
        "sentences": GOOD["sentences"]
        + [{"text": "감천문화마을에서도 촬영했습니다.", "entities": ["감천문화마을"]}]
    }
    svc, _ = service(reply)

    result = introduce(svc)

    assert result.status == "ok"
    assert len(result.sentences) == 3
    assert [d.detail for d in result.dropped] == ["감천문화마을"]


def test_too_few_sentences_after_check_degrades_to_synopsis() -> None:
    reply = {
        "sentences": [
            GOOD["sentences"][0],
            {"text": "청룡영화상을 받았습니다.", "entities": ["청룡영화상"]},
            {"text": "2002년 재개봉했습니다.", "entities": []},
        ]
    }
    svc, _ = service(reply)

    result = introduce(svc)

    assert (result.status, result.reason) == ("degraded", "TOO_FEW")  # FR-110: 2문장 미만
    assert result.fallback.synopsis == WORK.synopsis
    assert result.fallback.locations == WORK.locations
    assert not result.ai_generated and result.sentences == ()


def test_ai_failure_degrades_to_synopsis() -> None:  # AC-04
    svc, _ = service(ProviderError("upstream"))

    result = introduce(svc)

    assert (result.status, result.reason) == ("degraded", "AI_UNAVAILABLE")
    assert result.fallback.synopsis == WORK.synopsis


def test_partial_level_keeps_character_names_but_drops_ending() -> None:
    reply = {
        "sentences": [
            {"text": "1976년 부산에서 준석과 동수가 함께 자랍니다.", "entities": ["준석", "동수"]},
            {
                "text": "범일동 철길에서 네 친구가 뛰어가는 장면을 찍었습니다.",
                "entities": ["범일동 철길"],
            },
            {"text": "결말에서 동수는 죽음을 맞습니다.", "entities": ["동수"]},
        ]
    }
    svc, gemini = service(reply)

    result = introduce(svc, level="partial")

    assert result.status == "ok"
    assert result.sentences == (
        "1976년 부산에서 준석과 동수가 함께 자랍니다.",  # 줄거리 속 인물은 근거 줄에 있음
        "범일동 철길에서 네 친구가 뛰어가는 장면을 찍었습니다.",
    )
    assert result.dropped[0].reason == "spoiler"
    assert "줄거리: 1976년 부산" in gemini.calls[0].user


def test_unmatched_work_is_not_eligible() -> None:  # FR-108
    svc, gemini = service()
    unmatched = WorkFacts(work_id=40, title="광고 A", kmdb_matched=False)

    result = introduce(svc, work=unmatched)

    assert result.status == "not_eligible"
    assert gemini.calls == []


def test_work_without_locations_is_not_sent() -> None:
    svc, gemini = service()
    bare = WorkFacts(work_id=41, title="작품", kmdb_matched=True, synopsis="원문")

    result = introduce(svc, work=bare)

    assert (result.status, result.reason) == ("degraded", "NO_EVIDENCE")
    assert gemini.calls == []


def test_ai_calls_are_counted_for_request_log() -> None:  # NFR-005
    from app.support import request_context

    svc, _ = service(GOOD)
    ctx, token = request_context.begin("rid")
    try:
        introduce(svc)
    finally:
        request_context.end(token)

    assert ctx.ai_calls == {"gemini": 1}


# ── 작품 조회 → 근거 값(API-004) ─────────────────────────────
def test_work_facts_from_detail_row() -> None:
    from app.data.works import WorkDetailRow, WorkLocationRow, WorkRow
    from app.services.work_intro import work_facts

    row = WorkDetailRow(
        work=WorkRow(31, "친구", 2001, ["범죄"], "곽경택", 2, True, "DATA-01"),
        cast=["유오성", "장동건"],
        kmdb={"plot": "  1976년 부산.  ", "link": "https://www.kmdb.or.kr/x"},
        locations=[
            WorkLocationRow(1, "범일동 철길", "동구", 35.1, 129.0),
            WorkLocationRow(2, "어느 골목", "부산", None, None),  # 구·군 이름이 아님
        ],
        source_ids={"DATA-01"},
    )

    facts = work_facts(row)

    assert (facts.title, facts.kmdb_matched, facts.release_year) == ("친구", True, 2001)
    assert facts.cast == ("유오성", "장동건") and facts.genres == ("범죄",)
    assert facts.synopsis == "1976년 부산."
    assert facts.locations == (LocationFact("범일동 철길", "동구"), LocationFact("어느 골목"))
    no_kmdb = WorkDetailRow(row.work, [], None, [], set())
    assert work_facts(no_kmdb).synopsis is None


def test_place_without_busan_gu_is_not_called_busan() -> None:  # N-05: '부산의 은하사(김해)' 방지
    work = WorkFacts(
        work_id=9,
        title="달마야 놀자",
        kmdb_matched=True,
        locations=(LocationFact("은하사(김해)"), LocationFact("자갈치시장", "중구")),
    )
    facts = NoSpoilerEvidence().build(work).facts
    assert "촬영지: 은하사(김해)" in facts
    assert "촬영지: 자갈치시장 (부산 중구)" in facts
    assert not any(f.startswith("부산 촬영지") for f in facts)

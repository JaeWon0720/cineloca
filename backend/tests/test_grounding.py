"""근거 대조 검증기와 결과 요약 프롬프트 테스트.

COMP-09, BR-05, AI-REQ-01·06·07, FR-110·111·407
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from app.data.ai_gateway import TASKS, DefaultPromptBuilder, ProviderChain
from app.data.ai_gateway.prompts import SUMMARY_MAX_FACTS
from app.data.ai_gateway.tasks import GroundedSentence
from app.data.ai_gateway.testing import FakeProvider
from app.support.grounding import (
    Evidence,
    GroundingVerifier,
    NameLexicon,
    gate,
    normalize,
    numbers_in,
)
from app.support.privacy import mask

EVIDENCE = Evidence(
    facts=(
        "광안대교 (수영구) — 작품: 해운대(2009), 도보 10분",
        "자갈치시장 (중구) — 작품: 친구(2001), 국제시장(2014), 도보 7분",
        "범일동 철길 (동구) — 작품: 친구(2001)",
    ),
    names=frozenset(
        {
            "광안대교",
            "수영구",
            "해운대",
            "자갈치시장",
            "중구",
            "친구",
            "국제시장",
            "범일동 철길",
            "동구",
            "곽경택",
        }
    ),
)


@dataclass
class S:
    text: str
    entities: list[str] = field(default_factory=list)


def verify(*claims, **kw):
    return GroundingVerifier(**kw).verify(claims, EVIDENCE)


# ── 근거 게이트 ────────────────────────────────────────────
def test_gate_blocks_empty_evidence() -> None:
    assert gate(EVIDENCE)
    assert not gate(None)
    assert not gate(Evidence(facts=()))
    assert not gate(Evidence(facts=("  ",)))


# ── 문장 대조 ──────────────────────────────────────────────
def test_grounded_sentences_are_kept() -> None:
    result = verify(
        S("자갈치시장과 범일동 철길에서 친구를 찍었습니다.", ["자갈치시장", "범일동 철길", "친구"]),
        S("광안대교는 도보 10분 거리입니다.", ["광안대교"]),
    )

    assert result.sentences == (
        "자갈치시장과 범일동 철길에서 친구를 찍었습니다.",
        "광안대교는 도보 10분 거리입니다.",
    )
    assert result.dropped_count == 0


def test_entity_not_in_evidence_drops_sentence() -> None:
    result = verify(
        S("자갈치시장에서 친구를 찍었습니다.", ["자갈치시장", "친구"]),
        S("감천문화마을에서도 촬영했습니다.", ["감천문화마을"]),  # 근거에 없음
        S("장동건이 출연했습니다.", ["장동건"]),  # 근거에 없는 인명
    )

    assert result.sentences == ("자갈치시장에서 친구를 찍었습니다.",)
    assert [(d.reason, d.detail) for d in result.dropped] == [
        ("entity", "감천문화마을"),
        ("entity", "장동건"),
    ]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("친구는 2002년에 개봉했습니다.", "2002"),  # 근거는 2001
        ("광안대교까지 도보 15분입니다.", "15"),  # 근거는 10분
        ("촬영지가 5곳 있습니다.", "5"),  # 근거에 없는 개수
    ],
)
def test_number_not_in_evidence_drops_sentence(text: str, number: str) -> None:
    result = verify(S(text, []))

    assert result.sentences == ()
    assert [(d.reason, d.detail) for d in result.dropped] == [("number", number)]


def test_numbers_from_evidence_are_allowed() -> None:
    result = verify(
        S("친구(2001)와 국제시장(2014) 촬영지가 자갈치시장에 있습니다.", ["친구", "국제시장"])
    )

    assert result.dropped_count == 0
    assert numbers_in("2,001년 1.5km") == {"2001", "1.5"}


def test_partial_name_and_particles_are_supported() -> None:
    result = verify(
        S("해운대 근처입니다.", ["해운대"]),
        S("범일동 철길에서 찍었습니다.", ["범일동철길에서"]),  # 조사가 붙고 띄어쓰기가 달라도
        S("부산 곳곳에서 찍었습니다.", ["부산"]),  # 서비스 범위 이름은 항상 허용
    )

    assert result.dropped_count == 0


def test_names_inside_one_fact_line_pass_but_not_across_lines() -> None:
    evidence = Evidence(
        facts=("작품: 친구 (2001)", "부산 촬영지: 자갈치시장 (중구)", "줄거리: 준석과 동수")
    )

    result = GroundingVerifier().verify(
        [
            S("준석과 동수가 나옵니다.", ["준석", "동수"]),  # 줄거리 줄 안에 있음 → 통과
            S("가짜 이름입니다.", ["2001부산"]),  # 두 줄 경계를 넘는 문자열 → 삭제
            S("가짜 이름입니다.", ["중구줄거리"]),  # 둘째 줄 끝 + 셋째 줄 앞 → 삭제
        ],
        evidence,
    )

    assert result.sentences == ("준석과 동수가 나옵니다.",)
    assert [d.detail for d in result.dropped] == ["2001부산", "중구줄거리"]


def test_lexicon_catches_names_missing_from_entities() -> None:
    lexicon = NameLexicon(
        ["감천문화마을", "광안대교", "친구"]
    )  # '친구'는 min_len 3보다 짧아 빠진다

    result = verify(
        S("감천문화마을도 있습니다.", []),  # 모델이 entities에 안 적어도 사전으로 찾는다
        S("친구와 함께 가기 좋은 광안대교입니다.", ["광안대교"]),
        lexicon=lexicon,
    )

    assert result.sentences == ("친구와 함께 가기 좋은 광안대교입니다.",)
    assert result.dropped[0].detail == "감천문화마을"


@pytest.mark.parametrize(
    "text",
    [
        "친구의 결말은 슬픕니다.",
        "마지막에 범인은 친구였습니다.",
        "알고 보니 형제였습니다.",
        "반 전이 있습니다.",
    ],
)
def test_spoiler_phrases_drop_sentence(text: str) -> None:
    result = verify(S(text, []))

    assert result.sentences == ()
    assert result.dropped[0].reason == "spoiler"


def test_max_sentences_and_enough() -> None:
    # 숫자는 근거 값(10분, 7분, 2001)과 정확히 같아야 하므로 근거에 있는 값만 쓴다
    claims = [S(f"자갈치시장 소개 {n}.", ["자갈치시장"]) for n in (10, 7, 2001)]

    result = GroundingVerifier().verify(claims, EVIDENCE, max_sentences=2)

    assert len(result.sentences) == 2
    assert result.enough(2) and not result.enough(3)


def test_normalize() -> None:
    assert normalize(" 범일동 철길! ") == "범일동철길"
    assert normalize("ＡＢＣ") == "abc"


# ── 결과 요약 프롬프트 (FR-307) ────────────────────────────
def test_summary_prompt_uses_only_top_10_facts() -> None:
    many = Evidence(facts=tuple(f"촬영지{i} (중구)" for i in range(15)))

    prompt = DefaultPromptBuilder().build(
        TASKS["summarize_results"], {"query": mask("중구 촬영지"), "evidence": many}
    )

    assert prompt.user.startswith("질문: 중구 촬영지\n검색 결과(근거, 10건):")
    assert "- 촬영지9 (중구)" in prompt.user and "촬영지10" not in prompt.user
    assert SUMMARY_MAX_FACTS == 10
    assert "근거에 적힌 사실만" in prompt.system
    assert (prompt.version, prompt.temperature) == ("v1", 0.2)


def test_summary_requires_masked_query() -> None:
    chain = ProviderChain({}, DefaultPromptBuilder())

    with pytest.raises(TypeError):
        asyncio.run(
            chain.generate("summarize_results", {"query": "원문", "evidence": EVIDENCE}, 10)
        )


def test_summary_end_to_end_drops_hallucinated_sentence() -> None:
    reply = {
        "sentences": [
            {
                "text": "친구 촬영지는 자갈치시장과 범일동 철길입니다.",
                "entities": ["친구", "자갈치시장", "범일동 철길"],
            },
            {"text": "감독 윤제균의 작품도 있습니다.", "entities": ["윤제균"]},
        ]
    }
    chain = ProviderChain({"gemini": FakeProvider("gemini", [reply])}, DefaultPromptBuilder())

    result = asyncio.run(
        chain.generate(
            "summarize_results", {"query": mask("친구 촬영지"), "evidence": EVIDENCE}, 10
        )
    )
    checked = GroundingVerifier().verify(result.data.sentences, EVIDENCE, max_sentences=3)

    assert isinstance(result.data.sentences[0], GroundedSentence)
    assert checked.sentences == ("친구 촬영지는 자갈치시장과 범일동 철길입니다.",)
    assert checked.dropped[0].detail == "윤제균"

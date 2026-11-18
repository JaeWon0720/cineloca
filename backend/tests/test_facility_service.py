"""F4 코스 주변 시설 추천 테스트 (CR-01, FR-415~418)."""

import asyncio

import pytest
from pydantic import ValidationError

from app.core.facilities import PER_SEGMENT, WEIGHTS
from app.data.ai_gateway import TASKS, DefaultPromptBuilder, ProviderChain, ProviderError
from app.data.ai_gateway.tasks import FacilityConditions
from app.data.ai_gateway.testing import FakeProvider
from app.services.facility_service import (
    FacilityCandidate,
    FacilityInputError,
    FacilityPicker,
    FacilitySegment,
    FacilityService,
    conditions_from_selection,
    merge_selected,
    structure_conditions,
)
from app.support import request_context
from app.support.grounding import Evidence
from app.support.privacy import mask


def food(i: str, name: str, cat: str, d: int) -> FacilityCandidate:
    return FacilityCandidate(i, name, "food", cat, 35.1, 129.0, d)


def lodge(i: str, name: str, cat: str, d: int) -> FacilityCandidate:
    return FacilityCandidate(i, name, "lodging", cat, 35.1, 129.0, d)


SEG1 = FacilitySegment(
    "1-2",
    "food",
    "자갈치시장~범일동 철길 사이",
    ("자갈치시장", "범일동 철길"),
    (
        food("f1", "할매국밥", "밥", 300),
        food("f2", "오션뷰카페", "카페", 200),
        food("f3", "골목식당", "밥", 900),
        food("f4", "먼카페", "카페", 1500),  # 1km 밖
        food("f5", "돼지국밥집", "밥", 600),
    ),
)
LAST = FacilitySegment(
    "last",
    "lodging",
    "마지막 촬영지 범일동 철길",
    ("범일동 철길",),
    (lodge("l1", "부산호텔", "호텔", 400), lodge("l2", "바다게스트하우스", "게스트하우스", 150)),
)

COND_AI = {"kinds": [], "food": ["밥"], "lodging": [], "keywords": ["국밥"]}


async def _no_sleep(_s: float) -> None:
    return None


def make(*replies) -> tuple[FacilityService, FakeProvider]:
    gemini = FakeProvider("gemini", list(replies))
    chain = ProviderChain({"gemini": gemini}, DefaultPromptBuilder(), sleep=_no_sleep)
    return FacilityService(chain), gemini


def recommend(svc, **kw):
    return asyncio.run(svc.recommend((SEG1, LAST), **kw))


# ── 조건 구조화(FR-415) ─────────────────────────────────────
def test_no_text_does_not_call_ai() -> None:
    svc, gemini = make()

    cond = asyncio.run(structure_conditions(svc._client, None))  # noqa: SLF001

    assert cond.status == "selected" and cond.conditions == FacilityConditions()
    assert gemini.calls == []


def test_text_is_structured_and_selection_wins() -> None:
    svc, gemini = make(COND_AI)
    selected = FacilityConditions(food=["카페"])

    cond = asyncio.run(structure_conditions(svc._client, "국밥 먹고 싶어요", selected))  # noqa: SLF001

    assert cond.status == "ai"
    assert cond.conditions.food == ["카페"]  # 고른 분류가 문장보다 우선
    assert cond.conditions.keywords == ["국밥"]  # 고르지 않은 항목은 문장에서
    assert gemini.calls[0].task == "structure_facility"
    assert gemini.calls[0].user == "원하는 조건: 국밥 먹고 싶어요"


@pytest.mark.parametrize(
    "reply",
    [
        {**COND_AI, "food": ["분식"]},  # 목록 밖 분류
        {**COND_AI, "lodging": ["리조트"]},
        {**COND_AI, "kinds": ["shopping"]},
        {**COND_AI, "keywords": ["a", "b", "c", "d", "e", "f"]},  # 5개 초과
    ],
)
def test_schema_rejects_values_outside_lists(reply: dict) -> None:
    with pytest.raises(ValidationError):
        FacilityConditions.model_validate(reply)


def test_ai_failure_opens_manual_selection() -> None:  # FR-418
    svc, _ = make(ProviderError("timeout"))
    selected = FacilityConditions(lodging=["펜션"])

    cond = asyncio.run(structure_conditions(svc._client, "조용한 숙소", selected))  # noqa: SLF001

    assert cond.status == "manual" and cond.conditions == selected
    assert "직접 고를 수 있게" in cond.notices[-1]


def test_masked_text_only() -> None:
    svc, gemini = make(COND_AI)
    ctx, token = request_context.begin("rid")
    try:
        cond = asyncio.run(
            structure_conditions(svc._client, "010-1234-5678 국밥집 알려줘")  # noqa: SLF001
        )
    finally:
        request_context.end(token)

    assert cond.masked and "010-1234-5678" not in gemini.calls[0].user
    assert "010-1234-5678" not in (ctx.masked_query or "")


def test_input_errors() -> None:
    svc, _ = make()
    with pytest.raises(FacilityInputError):
        asyncio.run(structure_conditions(svc._client, "가" * 201))  # noqa: SLF001
    with pytest.raises(FacilityInputError):
        conditions_from_selection(food=["분식"])
    assert conditions_from_selection(kinds=["food"], food=["카페"]).food == ["카페"]


def test_merge_selected_keeps_ai_when_nothing_chosen() -> None:
    ai = FacilityConditions.model_validate(COND_AI)
    assert merge_selected(ai, None) == ai
    assert merge_selected(ai, FacilityConditions()) == ai


# ── 후보 선정(FR-416) ───────────────────────────────────────
def test_pick_filters_by_category_and_radius_and_ranks() -> None:
    cond = FacilityConditions(food=["밥"], keywords=["국밥"])

    seg, last = FacilityPicker().pick((SEG1, LAST), cond)

    assert [p.facility.id for p in seg.picks] == ["f1", "f5", "f3"]  # 밥만, 국밥 키워드·거리 순
    assert seg.picks[0].matched == ("분류: 밥", "키워드: 국밥")
    assert seg.picks[2].parts["match"] == 0.5  # 분류만 맞음
    assert [p.facility.id for p in last.picks] == [
        "l2",
        "l1",
    ]  # 숙박은 분류 안 골라서 모두, 거리 순


def test_pick_at_most_three_and_only_candidates() -> None:
    (seg,) = FacilityPicker().pick((SEG1,), FacilityConditions())

    assert len(seg.picks) == PER_SEGMENT == 3
    ids = {f.id for f in SEG1.candidates}
    assert {p.facility.id for p in seg.picks} <= ids  # 후보에 없는 곳은 없음
    assert "f4" not in {p.facility.id for p in seg.picks}  # 1km 밖


def test_kind_filter_and_empty_notice() -> None:
    cafe_only = FacilityConditions(kinds=["food"], food=["카페"])
    (seg,) = FacilityPicker().pick((SEG1, LAST), cafe_only)  # 숙박 구간은 건너뜀
    assert [p.facility.id for p in seg.picks] == ["f2"]

    (empty,) = FacilityPicker().pick((LAST,), FacilityConditions(lodging=["캠핑"]))
    assert empty.picks == () and "찾지 못했어요" in empty.notice


def test_same_facility_not_repeated_across_segments() -> None:
    seg2 = FacilitySegment(
        "2-3", "food", "범일동 철길~부산진역 사이", ("범일동 철길",), SEG1.candidates
    )

    a, b = FacilityPicker().pick((SEG1, seg2), FacilityConditions(food=["밥"]))

    assert not {p.facility.id for p in a.picks} & {p.facility.id for p in b.picks}


def test_weights() -> None:
    assert abs(sum(WEIGHTS.values()) - 1) < 1e-9
    with pytest.raises(ValueError):
        FacilityPicker({"match": 1.0})


# ── 추천 이유(FR-417·418) ───────────────────────────────────
def _reason(n: int, text: str, *entities: str) -> dict:
    return {"number": n, "text": text, "entities": list(entities)}


def test_reasons_are_grounded_or_replaced_by_template() -> None:
    first_text = "자갈치시장~범일동 철길 사이에서 300m 떨어진 할매국밥이에요."
    reasons = {
        "reasons": [
            _reason(1, first_text, "할매국밥", "자갈치시장", "범일동 철길"),
            _reason(2, "부산에서 유명한 돼지국밥집이에요.", "돼지국밥집"),  # 근거 없는 평가 → 삭제
            _reason(3, "골목식당은 도보 5분이에요.", "골목식당"),  # 숫자 5는 근거에 없음 → 삭제
            _reason(9, "없는 번호"),
        ]
    }
    svc, gemini = make(COND_AI, reasons)

    r = recommend(svc, text="국밥 먹고 싶어요")

    first, second, third = r.segments[0].picks
    assert (first.reason, first.ai_reason) == (first_text, True)
    assert not second.ai_reason and second.reason.endswith("(분류: 밥, 키워드: 국밥).")
    assert not third.ai_reason
    assert third.reason == "자갈치시장~범일동 철길 사이에서 900m 거리에 있는 식당이에요 (분류: 밥)."
    assert gemini.calls[1].task == "facility_reasons"
    assert "1. 할매국밥 (음식·밥) — 자갈치시장~범일동 철길 사이에서 300m" in gemini.calls[1].user


def test_reason_failure_keeps_recommendations() -> None:  # FR-418
    svc, _ = make(ProviderError("upstream"))

    r = recommend(svc)  # 문장 없음 → 구조화 안 부르고 이유만 부름(실패)

    assert r.conditions.status == "selected"
    assert all(p.reason and not p.ai_reason for s in r.segments for p in s.picks)
    assert [len(s.picks) for s in r.segments] == [3, 2]


def test_no_picks_skips_reason_call() -> None:
    svc, gemini = make()

    r = recommend(svc, selected=FacilityConditions(kinds=["lodging"], lodging=["캠핑"]))

    assert [s.picks for s in r.segments] == [()]
    assert gemini.calls == []


def test_template_grammar() -> None:
    from app.services.facility_service import SegmentPicks, template_reason

    seg = SegmentPicks("last", "lodging", "마지막 촬영지 범일동 철길", ())
    hotel = FacilityPicker().pick((LAST,), FacilityConditions())[0].picks[1]  # 부산호텔
    gh = FacilityPicker().pick((LAST,), FacilityConditions())[0].picks[0]

    assert (
        template_reason(seg, hotel) == "마지막 촬영지 범일동 철길에서 400m 거리에 있는 호텔이에요."
    )
    assert template_reason(seg, gh).endswith("게스트하우스예요.")


def test_reason_prompt() -> None:
    p = DefaultPromptBuilder().build(
        TASKS["facility_reasons"], {"evidence": Evidence(("1. 할매국밥 (음식·밥) — X에서 300m",))}
    )
    assert p.user == "근거:\n1. 할매국밥 (음식·밥) — X에서 300m"
    assert "맛·가격·분위기·평점" in p.system and p.version == "v1"
    s = DefaultPromptBuilder().build(TASKS["structure_facility"], {"text": mask("카페")})
    assert "밥, 카페" in s.system and "호텔, 콘도, 펜션, 모텔, 캠핑, 게스트하우스" in s.system

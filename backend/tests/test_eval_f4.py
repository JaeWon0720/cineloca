"""F4 시설 평가셋(AI-REQ-11)과 채점 테스트. 실제 공급자는 부르지 않는다."""

import asyncio

from app.core.facilities import FOOD_CATEGORIES, LODGING_CATEGORIES
from app.data.ai_gateway import DefaultPromptBuilder, ProviderChain
from app.data.ai_gateway.testing import FakeProvider
from scripts.eval_ai import (
    EVAL_DIR,
    facility_field_eq,
    grade_facility,
    load_jsonl,
    load_segments,
    reason_violations,
    run_f4,
    summarize_f4,
)

SEGMENTS = load_segments(EVAL_DIR / "f4_segments.json")


def test_f4_set_shape():
    items = load_jsonl(EVAL_DIR / "f4_facility.jsonl")
    assert len(items) == 30 and len({i["id"] for i in items}) == 30
    for item in items:
        g = item["gold"]
        assert set(g) == {"kinds", "food", "lodging"}
        assert set(g["kinds"]) <= {"food", "lodging"}
        assert set(g["food"]) <= set(FOOD_CATEGORIES)
        assert set(g["lodging"]) <= set(LODGING_CATEGORIES)
        assert len(item["text"]) <= 200


def test_segments_are_fictional_and_valid():
    assert [s.key for s in SEGMENTS] == ["1-2", "2-3", "last"]
    ids = [c.id for s in SEGMENTS for c in s.candidates]
    assert len(ids) == len(set(ids))
    for s in SEGMENTS:
        assert all(c.kind == s.kind and 0 <= c.distance_m <= 1000 for c in s.candidates)


def test_kinds_empty_means_both():
    assert facility_field_eq("kinds", [], ["food", "lodging"])
    assert facility_field_eq("kinds", ["lodging", "food"], [])
    assert not facility_field_eq("kinds", ["food"], [])
    assert facility_field_eq("food", ["카페", "밥"], ["밥", "카페"])
    assert not facility_field_eq("food", [], ["밥"])


def test_grade_facility():
    gold = {"kinds": ["food"], "food": ["밥", "카페"], "lodging": []}
    pred = {"kinds": ["food"], "food": ["밥"], "lodging": [], "keywords": ["빵"]}
    assert grade_facility(gold, pred)["wrong"] == ["food"]
    assert grade_facility(gold, pred, {"food": [["밥"]]})["fields_ok"] == 3
    none = grade_facility(gold, None)
    assert none == {"fields_ok": 0, "fields": 3, "wrong": ["kinds", "food", "lodging"]}


def test_reason_violations():
    names = {"A": "남포 돼지국밥", "B": "바다 커피로스터스"}
    picks = [
        ("A", "자갈치시장에서 180m 거리의 남포 돼지국밥이에요.", 180, True),
        ("B", "0.24km 거리의 카페예요.", 240, True),
        ("A", "1,800m 떨어진 식당이에요.", 180, True),  # 거리 다름
        ("B", "남포 돼지국밥 옆 카페예요.", 240, True),  # 다른 시설 이름
        ("B", "아무 말이나 450m", 240, False),  # 템플릿 문장은 보지 않음
        ("Z", "후보 밖", 10, True),
    ]
    outside, bad, notes = reason_violations(picks, names)
    assert (outside, bad) == (1, 2) and len(notes) == 3


def _chain(script) -> ProviderChain:
    return ProviderChain(
        {"gemini": FakeProvider("gemini", script)},
        DefaultPromptBuilder(),
        sleep=lambda _s: asyncio.sleep(0),
    )


def test_run_f4_end_to_end_with_fake_provider():
    cafe = {"kinds": ["food"], "food": ["카페"], "lodging": []}
    hotel = {"kinds": ["lodging"], "food": [], "lodging": ["호텔"]}
    items = [
        {"id": "T1", "text": "커피 한잔할 곳", "gold": cafe},
        {"id": "T2", "text": "010-1111-2222 호텔", "gold": hotel},
    ]
    script = [
        {"kinds": ["food"], "food": ["카페"], "lodging": [], "keywords": []},  # T1 구조화
        "형식 아님",  # T1 추천 이유: 스키마 위반 두 번 → 템플릿 문장
        "또 형식 아님",
        "형식 아님",  # T2 구조화 실패 두 번 → 직접 고르기(manual)
        "또 형식 아님",
    ]
    rows, stopped = asyncio.run(run_f4(_chain(script), items, SEGMENTS))
    assert stopped is None
    assert rows[0]["status"] == "ai" and rows[0]["fields_ok"] == 3
    assert rows[0]["reasons"] == 6 and rows[0]["ai_reasons"] == 0  # 음식 구간 2개 × 3곳
    assert rows[1]["status"] == "manual" and rows[1]["pred"] is None
    assert "1111" not in rows[1]["text_masked"]
    s = summarize_f4(rows)
    assert s["field_match_pct"] == 50.0 and s["picks_outside_candidates"] == 0
    assert s["reasons_not_grounded"] == 0 and s["pass"] is False

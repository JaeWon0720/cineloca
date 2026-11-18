"""역간 소요시간 미리 계산 단위 테스트 (FR-313, BR-12, DATA-09).

실제 응답 사본(tests/fixtures/real/data09_getDistance.json, 2026-11-18 수집,
이용허락범위 제한 없음)과 손으로 만든 작은 구간을 쓴다. 실제 API는 부르지 않는다.
"""

import json
from pathlib import Path

from app.batch import station_times as st
from app.batch.collect import find_items
from app.batch.stations import Station

REAL = Path(__file__).parent / "fixtures" / "real" / "data09_getDistance.json"


def real_rows() -> list[dict]:
    items, total = find_items(json.loads(REAL.read_text(encoding="utf-8")), "startSc")
    assert total == len(items) == 328
    return items


def stn(code: str) -> Station:
    return Station(code, code, "1호선", 35.1, 129.0)


def leg(a: str, b: str, sec: str = "60", stop: str = "0") -> dict:
    return {"startSc": a, "endSc": b, "time": sec, "stoppingTime": stop}


def test_parse_real_response() -> None:
    legs, skipped = st.parse(real_rows())
    assert len(legs) == 328 and skipped == 0
    by = {(x.from_id, x.to_id): x.seconds for x in legs}
    assert by[("100", "101")] == 180 + 30  # 동매 → 신평: 이동 180초 + 정차 30초
    assert by[("119", "219")] == 120  # 서면 1호선 → 2호선 환승 통로(exchange Y)


def test_real_line1_end_to_end_close_to_timetable() -> None:
    """다대포해수욕장(95) → 노포(134) 79.1분. 같은 기관 시간표(노포 05:08 → 다대포해수욕장 06:28,
    80분)와 맞고, 이동시간만 더하면(62.4분) 짧게 나온다."""
    legs, _ = st.parse(real_rows())
    codes = sorted({x.from_id for x in legs} | {x.to_id for x in legs}, key=int)
    m = {(a, b): v for a, b, v in st.all_pairs(legs, [stn(c) for c in codes])}
    assert len(m) == len(codes) * (len(codes) - 1)  # 동해선·경전철 포함 모두 이어짐
    assert m[("95", "134")] == 79.1
    assert m[("119", "219")] == 2.0
    assert m[("116", "816")] > 30  # 1호선 좌천과 동해선 좌천은 이름만 같은 다른 역


def test_parse_skips_bad_rows() -> None:
    rows = [leg("1", "2"), leg("", "2"), leg("3", "3"), {"startSc": "4", "endSc": "5", "time": "x"}]
    legs, skipped = st.parse(rows)
    assert [(x.from_id, x.to_id) for x in legs] == [("1", "2")] and skipped == 3


def test_one_way_leg_mirrored_and_duplicate_keeps_shorter() -> None:
    legs, _ = st.parse([leg("1", "2", "120"), leg("2", "3", "60"), leg("2", "3", "30")])
    g = st.graph(legs)
    assert g["2"]["1"] == 2.0  # 반대 방향 줄이 없으면 같은 시간
    assert g["2"]["3"] == 0.5 and g["3"]["2"] == 0.5


def test_given_reverse_direction_is_not_overwritten() -> None:
    legs, _ = st.parse([leg("1", "2", "60"), leg("2", "1", "120")])
    g = st.graph(legs)
    assert (g["1"]["2"], g["2"]["1"]) == (1.0, 2.0)


def test_all_pairs_only_known_stations_but_paths_through_unknown() -> None:
    legs, _ = st.parse([leg("1", "X", "60"), leg("X", "2", "60"), leg("8", "9", "60")])
    pairs = st.all_pairs(legs, [stn("1"), stn("2"), stn("8")])
    assert pairs == [("1", "2", 2.0), ("2", "1", 2.0)]  # X는 목록 밖, 8은 다른 역과 안 이어짐

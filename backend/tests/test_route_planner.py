"""코스 경로 계산 테스트 (FR-404·405·413, BR-12, UC-15)."""

import random

import pytest

from app.data.station_times import StationTimes
from app.data.stations import Network, StationPoint
from app.services.route_planner import (
    Point,
    RoutePlanner,
    TravelTimeEstimator,
    best_order,
    brute_force,
    path_cost,
    walk_minutes,
)

M = 1 / 111_195  # 위도 1m
BASE = Point(35.10, 129.03)


def north(m: float, p: Point = BASE) -> Point:
    return Point(p.lat + m * M, p.lon)


# 남북 일직선 위 역 3곳(같은 호선): A(0m) - B(3km) - C(6km)
STATIONS = [
    StationPoint("101", "에이", "1호선", BASE.lat, BASE.lon),
    StationPoint("102", "비", "1호선", north(3000).lat, BASE.lon),
    StationPoint("103", "씨", "1호선", north(6000).lat, BASE.lon),
]
TIMES = StationTimes(
    {("101", "103"): 6.0, ("103", "101"): 6.0, ("101", "102"): 3.0, ("102", "101"): 3.0}
)


def test_walk_formula() -> None:
    assert walk_minutes(1000) == pytest.approx(1000 * 1.3 / (4000 / 60))  # 약 19.5분


def test_short_hop_is_always_walk() -> None:
    leg = TravelTimeEstimator(Network(STATIONS, TIMES)).leg(BASE, north(700))

    assert leg.mode == "walk" and leg.distance_m == pytest.approx(700, abs=1)


def test_long_hop_uses_subway_when_faster() -> None:
    est = TravelTimeEstimator(Network(STATIONS, TIMES))
    leg = est.leg(north(50), north(5950))  # 역 근처에서 역 근처로 5.9km

    assert leg.mode == "subway"
    assert (leg.board.id, leg.alight.id) == ("101", "103")  # type: ignore[union-attr]
    assert leg.ride_min == 6.0
    assert leg.minutes == pytest.approx(walk_minutes(50) * 2 + 6.0)
    assert leg.minutes < walk_minutes(5900)


def test_subway_not_used_when_slower_or_missing() -> None:
    far_from_station = Point(BASE.lat, BASE.lon + 0.03)  # 역에서 동쪽 약 2.7km
    est = TravelTimeEstimator(Network(STATIONS, TIMES))
    no_times = TravelTimeEstimator(Network(STATIONS, StationTimes()))

    assert est.leg(far_from_station, north(1500, far_from_station)).mode == "walk"
    assert no_times.leg(north(50), north(5950)).mode == "walk"  # 역간 시간이 없으면 도보(UC-15)
    assert not no_times.has_subway


def random_cost(n: int, rng: random.Random) -> dict[tuple[int, int], float]:
    pts = [Point(35.05 + rng.random() * 0.15, 128.95 + rng.random() * 0.25) for _ in range(n)]
    est = TravelTimeEstimator()
    return {(i, j): est.leg(pts[i], pts[j]).minutes for i in range(n) for j in range(n) if i != j}


def test_fr404_within_10_percent_of_optimum_in_30_cases() -> None:
    rng = random.Random(404)
    ratios = []
    for case in range(30):
        n = 3 + case % 7  # 출발지 + 장소 2~8곳
        cost = random_cost(n, rng)
        todo = list(range(1, n))
        got = path_cost(0, best_order(0, todo, cost), cost)
        ratios.append(got / brute_force(0, todo, cost))

    assert max(ratios) <= 1.10  # FR-404: 완전 탐색 최적해 대비 +10% 이내
    assert sum(ratios) / len(ratios) < 1.02


def test_planner_orders_from_start_and_keeps_fixed_order() -> None:
    a, b, c = north(300), north(900), north(600)
    planner = RoutePlanner(TravelTimeEstimator())

    best = planner.plan(BASE, [a, b, c])
    fixed = planner.plan(BASE, [a, b, c], fixed_order=True)
    from_first = planner.plan(None, [b, a, c])

    assert best.order == [0, 2, 1]  # 300 → 600 → 900m
    assert fixed.order == [0, 1, 2] and fixed.travel_min > best.travel_min
    assert from_first.order[0] == 0  # 출발지가 없으면 첫 장소 고정
    assert len(best.legs) == 3 and len(from_first.legs) == 2


def test_stored_nearest_station_is_used_for_locations() -> None:
    est = TravelTimeEstimator(Network(STATIONS, TIMES))
    # 촬영지는 C1이 계산해 둔 최근접 역·도보 분을 쓴다(FR-606): 일부러 3분으로 둠
    a = Point(north(50).lat, BASE.lon, "101", 3.0)
    b = Point(north(5950).lat, BASE.lon, "103", 3.0)

    leg = est.leg(a, b)

    assert leg.mode == "subway" and leg.minutes == pytest.approx(3.0 + 6.0 + 3.0)

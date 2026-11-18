"""코스 경로 계산 (COMP-05 RoutePlanner·TravelTimeEstimator, FR-404·405·413, BR-12, UC-15).

이동 시간(BR-12)
- 도보: 직선거리 × 1.3 ÷ 시속 4km.
- 도보 거리(직선 × 1.3)가 1km를 넘으면 도시철도 시간 = 출발지 최근접 역까지 도보 + 역간 소요시간
  + 도착역에서 도보 를 구해 짧은 쪽을 쓴다. 역간 소요시간이 없으면 도보만 쓴다(UC-15).
방문 순서(FR-404)
- 최근접 이웃으로 순서를 정하고, 2-opt 교환과 Or-opt(1~3곳 묶음 옮기기)로 총 이동 시간이 더 줄지
  않을 때까지 고친다. 첫 장소를 바꿔 가며(최대 10번) 이 과정을 돌려 가장 짧은 것을 고른다.
  끝점은 자유(돌아오지 않음). 순서 고정(FR-405)이면 받은 순서대로 계산만 한다.
- 최근접 이웃 + 2-opt만으로는 무작위 30사례에서 최적해 대비 +21%가 나와 FR-404(+10%)를 못 넘었다.
  위 방식은 6,000사례에서 최악 +5.9%, 10곳 계산 약 0.01초(#76).
DB·네트워크를 모르는 순수 계산이라 단위 테스트로 검증한다(SDD 3.3 시험 용이성).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import permutations
from typing import Literal

from app.core.geo import haversine_m
from app.data.stations import Network, StationPoint

DETOUR = 1.3
WALK_M_PER_MIN = 4000 / 60
SUBWAY_FROM_WALK_M = 1000  # 도보 거리가 이보다 길면 도시철도와 비교(BR-12)


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    station_id: str | None = None  # 촬영지는 C1이 계산해 둔 최근접 역(FR-606)
    station_walk_min: float | None = None


@dataclass(frozen=True)
class Leg:
    mode: Literal["walk", "subway"]
    minutes: float
    distance_m: float  # 두 지점 직선거리
    board: StationPoint | None = None  # 도시철도: 타는 역
    alight: StationPoint | None = None  # 내리는 역
    walk_to_board_min: float = 0.0
    ride_min: float = 0.0
    walk_from_alight_min: float = 0.0


def walk_minutes(meters: float) -> float:
    return meters * DETOUR / WALK_M_PER_MIN


class TravelTimeEstimator:
    def __init__(self, network: Network | None = None) -> None:
        self._net = network or Network()

    @property
    def has_subway(self) -> bool:
        return bool(self._net.stations and len(self._net.times))

    def station_of(self, p: Point) -> tuple[StationPoint, float] | None:
        """(최근접 역, 그 역까지 도보 분). 촬영지는 저장된 값, 현재 위치·역은 여기서 계산한다."""
        by_id = self._by_id
        if p.station_id in by_id and p.station_walk_min is not None:
            return by_id[p.station_id], p.station_walk_min  # type: ignore[index]
        best = min(
            self._net.stations,
            key=lambda s: haversine_m(p.lat, p.lon, s.lat, s.lon),
            default=None,
        )
        if best is None:
            return None
        return best, walk_minutes(haversine_m(p.lat, p.lon, best.lat, best.lon))

    @property
    def _by_id(self) -> dict[str, StationPoint]:
        return {s.id: s for s in self._net.stations}

    def leg(self, a: Point, b: Point) -> Leg:
        straight = haversine_m(a.lat, a.lon, b.lat, b.lon)
        walk = Leg("walk", walk_minutes(straight), straight)
        if straight * DETOUR <= SUBWAY_FROM_WALK_M or not self.has_subway:
            return walk
        na, nb = self.station_of(a), self.station_of(b)
        if na is None or nb is None or na[0].id == nb[0].id:
            return walk
        ride = self._net.times.between(na[0].id, nb[0].id)
        if ride is None:
            return walk
        to_board, from_alight = na[1], nb[1]
        total = to_board + ride + from_alight
        if total >= walk.minutes:
            return walk
        return Leg("subway", total, straight, na[0], nb[0], to_board, ride, from_alight)


@dataclass(frozen=True)
class Route:
    order: list[int]  # stops 순번(0부터) 방문 순서
    legs: list[Leg]  # 출발지 → 첫 장소, 그다음 장소 사이(출발지가 없으면 장소 사이만)
    travel_min: float


class RoutePlanner:
    """plan(start, stops, fixed_order) — SDD 3.4 RoutePlanner 인터페이스."""

    def __init__(self, estimator: TravelTimeEstimator) -> None:
        self._est = estimator

    def plan(self, start: Point | None, stops: Sequence[Point], fixed_order: bool = False) -> Route:
        """start가 None이면 첫 장소에서 출발한다(첫 장소는 고정)."""
        if not stops:
            raise ValueError("장소가 없습니다")
        points = [start, *stops] if start else list(stops)
        legs = {
            (i, j): self._est.leg(points[i], points[j])
            for i in range(len(points))
            for j in range(len(points))
            if i != j
        }
        cost = {k: v.minutes for k, v in legs.items()}
        off = 1 if start else 0  # points 순번 = stops 순번 + off
        rest = list(range(off, len(points)))
        if fixed_order:
            seq = rest
        else:
            head = [] if start else [rest.pop(0)]  # 출발지가 없으면 첫 장소 고정
            first = 0 if start else head[0]
            seq = head + best_order(first, rest, cost)
        path = ([0] if start else []) + seq
        route_legs = [legs[(a, b)] for a, b in zip(path[:-1], path[1:], strict=True)]
        return Route(
            order=[i - off for i in seq],
            legs=route_legs,
            travel_min=sum(leg.minutes for leg in route_legs),
        )


def path_cost(first: int, seq: Sequence[int], cost: dict[tuple[int, int], float]) -> float:
    total, prev = 0.0, first
    for i in seq:
        if i != prev:
            total += cost[(prev, i)]
        prev = i
    return total


def nearest_neighbor(
    first: int, todo: Sequence[int], cost: dict[tuple[int, int], float]
) -> list[int]:
    left, seq, cur = list(todo), [], first
    while left:
        nxt = min(left, key=lambda j: (cost[(cur, j)], j))
        seq.append(nxt)
        left.remove(nxt)
        cur = nxt
    return seq


def two_opt(first: int, seq: list[int], cost: dict[tuple[int, int], float]) -> list[int]:
    """구간 뒤집기로 줄어들면 바꾸고, 더 줄지 않을 때까지 반복(열린 경로, 출발점 고정)."""
    best, best_cost = list(seq), path_cost(first, seq, cost)
    improved = True
    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                c = path_cost(first, cand, cost)
                if c < best_cost - 1e-9:
                    best, best_cost, improved = cand, c, True
    return best


def or_opt(first: int, seq: list[int], cost: dict[tuple[int, int], float]) -> list[int]:
    """1~3곳 묶음을 (뒤집어서도) 다른 자리로 옮겨 줄어들면 바꾼다."""
    best, best_cost = list(seq), path_cost(first, seq, cost)
    improved = True
    while improved:
        improved = False
        for size in (1, 2, 3):
            for i in range(len(best) - size + 1):
                seg, rest = best[i : i + size], best[:i] + best[i + size :]
                for j in range(len(rest) + 1):
                    for piece in (seg, seg[::-1]):
                        cand = rest[:j] + piece + rest[j:]
                        c = path_cost(first, cand, cost)
                        if c < best_cost - 1e-9:
                            best, best_cost, improved = cand, c, True
    return best


def local_search(first: int, seq: list[int], cost: dict[tuple[int, int], float]) -> list[int]:
    while True:
        before = path_cost(first, seq, cost)
        seq = or_opt(first, two_opt(first, seq, cost), cost)
        if path_cost(first, seq, cost) >= before - 1e-9:
            return seq


def best_order(first: int, todo: Sequence[int], cost: dict[tuple[int, int], float]) -> list[int]:
    """첫 장소를 바꿔 가며 최근접 이웃 + 2-opt·Or-opt를 돌리고 가장 짧은 순서를 고른다."""
    if not todo:
        return []
    tries = [
        local_search(first, [k, *nearest_neighbor(k, [x for x in todo if x != k], cost)], cost)
        for k in todo
    ]
    return min(tries, key=lambda s: (path_cost(first, s, cost), s))


def brute_force(first: int, todo: Sequence[int], cost: dict[tuple[int, int], float]) -> float:
    """완전 탐색 최적값(FR-404 검증용, 8곳 이하)."""
    return min(path_cost(first, p, cost) for p in permutations(todo))

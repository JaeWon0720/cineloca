"""역간 소요시간 미리 계산 (FR-313, BR-12, NFR-001). 원본은 DATA-09.

DATA-09(부산교통공사 역간 거리 및 소요시간 정보)는 이웃한 두 역 사이를 방향별로 한 줄씩 준다.
  startSc·endSc 역 코드(DATA-08과 같음), time 이동 초, stoppingTime 정차 초, dist 0.1km,
  exchange: Y = 환승 통로(같은 역의 다른 호선, 예: 서면 119 → 219 120초), N = 일반 구간
  (포털 명세의 'N = 경전철, 빈칸 = 일반역' 설명과 달리 실제 응답은 위와 같다. 2026-11-18 응답 사본
   tests/fixtures/real/data09_getDistance.json)
동해선·부산김해경전철 구간과 환승 통로도 들어 있다.

배치는 이 구간들로 모든 역 쌍의 최소 소요시간(분)을 구해 station_time에 넣는다.
코스 계산(TravelTimeEstimator, R1)은 표를 읽어 찾기만 한다(app/data/station_times.py).
- 한 구간 = 이동시간 + 정차시간. 정차시간은 출발역 기준 값이라 경로에는 출발역·중간역의
  정차가 들어간다.
- 환승은 원본의 환승 통로 구간만 쓴다. 이름만 같은 역(예: 1호선 좌천과 동해선 좌천)은 다른 역이라
  잇지 않는다.
- 반대 방향 줄이 없으면 같은 시간으로 본다. 같은 구간이 여러 번 오면 짧은 쪽을 쓴다.
"""

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.batch.stations import Station


@dataclass(frozen=True)
class Leg:
    from_id: str
    to_id: str
    seconds: float


def _sec(v) -> float | None:
    try:
        s = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return s if s >= 0 else None


def parse(rows: Iterable[Mapping]) -> tuple[list[Leg], int]:
    """원본 줄 → 구간. 코드·이동시간이 없거나 숫자가 아닌 줄은 건너뛰고 그 수를 함께 준다."""
    legs: list[Leg] = []
    skipped = 0
    for r in rows:
        a, b = str(r.get("startSc") or "").strip(), str(r.get("endSc") or "").strip()
        run = _sec(r.get("time"))
        if not a or not b or a == b or run is None:
            skipped += 1
            continue
        legs.append(Leg(a, b, run + (_sec(r.get("stoppingTime")) or 0.0)))
    return legs, skipped


def graph(legs: Iterable[Leg]) -> dict[str, dict[str, float]]:
    """역 코드 → {이웃 역 코드: 분}."""
    legs = list(legs)
    given = {(leg.from_id, leg.to_id) for leg in legs}
    g: dict[str, dict[str, float]] = {}

    def add(a: str, b: str, minutes: float) -> None:
        cur = g.setdefault(a, {}).get(b)
        if cur is None or minutes < cur:
            g[a][b] = minutes
        g.setdefault(b, {})

    for leg in legs:
        add(leg.from_id, leg.to_id, leg.seconds / 60)
        if (leg.to_id, leg.from_id) not in given:
            add(leg.to_id, leg.from_id, leg.seconds / 60)
    return g


def shortest_from(g: Mapping[str, Mapping[str, float]], start: str) -> dict[str, float]:
    """한 역에서 갈 수 있는 모든 역까지 최소 분(다익스트라)."""
    best = {start: 0.0}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > best[u]:
            continue
        for v, w in g.get(u, {}).items():
            if d + w < best.get(v, float("inf")):
                best[v] = d + w
                heapq.heappush(heap, (d + w, v))
    return best


def all_pairs(legs: Iterable[Leg], stations: Iterable[Station]) -> list[tuple[str, str, float]]:
    """station_time 행 (출발 역, 도착 역, 분). 두 역 모두 역 목록(station 표)에 있는 쌍만 넣는다
    (외래 키). 경로는 목록 밖 역(예: 부산 밖 2호선 양산 구간)도 지날 수 있다."""
    g = graph(legs)
    ids = sorted({s.id for s in stations})
    known = set(ids)
    out = []
    for a in ids:
        for b, d in sorted(shortest_from(g, a).items()):
            if b != a and b in known:
                out.append((a, b, round(d, 1)))
    return out

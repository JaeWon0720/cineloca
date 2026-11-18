"""도시철도역 좌표와 촬영지 최근접 역 (FR-606, BR-12).

DATA-08(부산교통공사 역사 정보)에는 호선과 좌표가 없다.
- 좌표: 역 주소를 촬영지와 같은 방법(DATA-06)으로 바꾼다.
- 호선: 역 코드 범위로 정한다. 95~134 = 1호선, 2xx = 2호선, 3xx = 3호선, 4xx = 4호선,
  8xx = 동해선, 9xx = 부산김해경전철
  (코드 표와 역 이름으로 맞춰 본 값. 공식 노선 원본이 생기면 바꾼다)
도보 시간 = 직선거리 × 1.3(굴곡 계수) ÷ 시속 4km.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass

WALK_M_PER_MIN = 4000 / 60
DETOUR = 1.3


def line_of(code: str) -> str | None:
    if not code.isdigit():
        return None
    n = int(code)
    if 95 <= n <= 134:
        return "1호선"
    return {2: "2호선", 3: "3호선", 4: "4호선", 8: "동해선", 9: "부산김해경전철"}.get(n // 100)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def walk_min(meters: float) -> float:
    return round(meters * DETOUR / WALK_M_PER_MIN, 1)


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    line: str
    lat: float
    lon: float
    gu: str | None = None


def nearest(lat: float, lon: float, stations: Iterable[Station]) -> tuple[Station, float] | None:
    """가장 가까운 역과 도보 분."""
    best = min(stations, key=lambda s: distance_m(lat, lon, s.lat, s.lon), default=None)
    if best is None:
        return None
    return best, walk_min(distance_m(lat, lon, best.lat, best.lon))

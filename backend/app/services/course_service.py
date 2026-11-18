"""코스 업무 로직 (COMP-05 CourseService, FR-401~405·413, BR-12·13, API-009~011).

- 나만의 코스(plan_custom): 출발지(현재 위치·역·촬영지) + 좌표 있는 촬영지 2~10곳.
  방문 순서를 최적화하거나(FR-404) 받은 순서대로(FR-405) 이동·체류·총 소요 시간을 계산한다.
- 현재 위치 좌표는 경로 계산에만 쓰고 응답·로그·저장 어디에도 남기지 않는다(BR-10).
- 장소별 체류 시간은 20분(BR-13). 구간 거리는 두 지점 직선거리다.
- 역·역간 소요시간은 DB에서 읽어 10분 동안 메모리에 둔다(코스 요청마다 다시 읽지 않게).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.core.names import station_label
from app.core.places import DISTRICT_CENTERS
from app.data import db
from app.data import locations as location_repo
from app.data import stations as station_repo
from app.data.stations import Network, StationPoint
from app.services.map_service import Connect, cert_info
from app.services.route_planner import Leg, Point, RoutePlanner, TravelTimeEstimator

STAY_MIN = 20  # BR-13
MIN_STOPS, MAX_STOPS = 2, 10  # FR-403
NETWORK_TTL_S = 600


class LocationNotFound(Exception):
    def __init__(self, ids: Sequence[int]) -> None:
        super().__init__(str(list(ids)))
        self.ids = list(ids)


class LocationNotLocated(Exception):
    """좌표가 없는 촬영지는 코스에 넣을 수 없다(FR-403·210)."""

    def __init__(self, ids: Sequence[int]) -> None:
        super().__init__(str(list(ids)))
        self.ids = list(ids)


class StationNotFound(Exception):
    pass


class InvalidStops(ValueError):
    """장소 수가 2~10곳이 아님(출발 촬영지 포함)."""


@dataclass(frozen=True)
class StartSpec:
    type: Literal["current", "station", "location"]
    lat: float | None = None  # current만
    lon: float | None = None
    id: str | int | None = None  # station(역 코드) 또는 location(촬영지 ID)


NetworkLoader = Callable[[], Awaitable[Network]]
_cache: tuple[float, Network] | None = None


def reset_network_cache() -> None:
    """역·역간 소요시간을 다시 읽게 한다(배치 반영 직후·테스트)."""
    global _cache
    _cache = None


def _station(s: StationPoint | None) -> dict[str, Any] | None:
    return {"id": s.id, "name": s.name, "line": s.line} if s else None


def _leg(leg: Leg, from_order: int, to_order: int) -> dict[str, Any]:
    return {
        "from_order": from_order,
        "to_order": to_order,
        "mode": leg.mode,
        "minutes": max(1, round(leg.minutes)) if leg.distance_m > 0 else 0,
        "distance_m": round(leg.distance_m),
        "board": _station(leg.board),
        "alight": _station(leg.alight),
        "walk_to_board_min": round(leg.walk_to_board_min),
        "ride_min": round(leg.ride_min),
        "walk_from_alight_min": round(leg.walk_from_alight_min),
    }


class CourseService:
    def __init__(self, connect: Connect = db.connection, network: NetworkLoader | None = None):
        self._connect = connect
        self._network = network or self._cached_network

    async def _cached_network(self) -> Network:
        global _cache
        now = time.monotonic()
        if _cache is None or now - _cache[0] > NETWORK_TTL_S:
            async with self._connect() as conn:
                _cache = (now, await station_repo.load_network(conn))
        return _cache[1]

    async def plan_custom(
        self, start: StartSpec, location_ids: Sequence[int], fixed_order: bool = False
    ) -> dict[str, Any]:
        """API-011. start가 촬영지면 그 촬영지가 첫 방문 장소다(목록에 없으면 앞에 붙인다)."""
        ids = list(dict.fromkeys(location_ids))
        if start.type == "location":
            sid = int(start.id)  # type: ignore[arg-type]
            ids = [sid, *[i for i in ids if i != sid]]
        if not MIN_STOPS <= len(ids) <= MAX_STOPS:
            raise InvalidStops(f"촬영지는 {MIN_STOPS}~{MAX_STOPS}곳이어야 합니다")
        async with self._connect() as conn:
            targets = await location_repo.cert_targets(conn, ids)
            station = (
                await station_repo.get_station(conn, str(start.id))
                if start.type == "station"
                else None
            )
        missing = [i for i in ids if i not in targets]
        if missing:
            raise LocationNotFound(missing)
        unlocated = [i for i in ids if targets[i].lat is None or targets[i].lon is None]
        if unlocated:
            raise LocationNotLocated(unlocated)
        if start.type == "station" and station is None:
            raise StationNotFound
        network = await self._network()

        start_info: dict[str, Any]
        origin: Point | None
        if start.type == "current":
            origin = Point(start.lat, start.lon)  # type: ignore[arg-type]
            start_info = {"type": "current", "label": "현재 위치", "lat": None, "lon": None}
        elif start.type == "station":
            assert station is not None
            origin = Point(station.lat, station.lon, station.id, 0.0)  # 그 역에서 바로 탐
            start_info = {
                "type": "station",
                "label": f"{station_label(station.name)}({station.line})",  # 서면(1) → 서면역
                "lat": station.lat,
                "lon": station.lon,
            }
        else:
            origin = None  # 첫 장소에서 출발
            first = targets[ids[0]]
            start_info = {
                "type": "location",
                "label": first.name,
                "lat": first.lat,
                "lon": first.lon,
            }

        points = [
            Point(
                targets[i].lat,  # type: ignore[arg-type]
                targets[i].lon,  # type: ignore[arg-type]
                targets[i].nearest_station_id,
                targets[i].station_walk_min,
            )
            for i in ids
        ]
        estimator = TravelTimeEstimator(network)
        route = RoutePlanner(estimator).plan(origin, points, fixed_order)
        result = self._result(
            start_info, [ids[i] for i in route.order], targets, route.legs, origin
        )
        return {**result, "optimized": not fixed_order, "subway_data": estimator.has_subway}

    def _result(
        self,
        start_info: dict[str, Any],
        visit: list[int],
        targets: dict[int, location_repo.CertTarget],
        legs: list[Leg],
        origin: Point | None,
    ) -> dict[str, Any]:
        stops = []
        for n, i in enumerate(visit, start=1):
            t = targets[i]
            stops.append(
                {
                    "order": n,
                    "id": t.id,
                    "name": t.name,
                    "gu": t.gu if t.gu in DISTRICT_CENTERS else None,  # BR-15 밖 값은 비움
                    "lat": t.lat,
                    "lon": t.lon,
                    "stay_min": STAY_MIN,
                    "cert": cert_info(True, t.caution, t.cert_excluded, t.cert_radius_m),
                }
            )
        # 출발지가 따로 있으면 0번 → 1번 구간부터, 촬영지 출발이면 1번 → 2번부터
        orders = ([0] if origin else []) + list(range(1, len(visit) + 1))
        leg_items = [
            _leg(leg, a, b) for leg, a, b in zip(legs, orders[:-1], orders[1:], strict=True)
        ]
        travel = sum(item["minutes"] for item in leg_items)
        stay = STAY_MIN * len(stops)
        return {
            "start": start_info,
            "stops": stops,
            "legs": leg_items,
            "travel_min": travel,
            "stay_min": stay,
            "total_min": travel + stay,
            "total_distance_m": sum(item["distance_m"] for item in leg_items),
        }

"""도시철도역 조회와 코스 계산용 묶음 (COMP-12, BR-12, FR-413).

역간 소요시간은 배치(#84)가 DATA-09로 미리 계산한 station_time을 app/data/station_times.py로 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg

from app.data import station_times
from app.data.station_times import StationTimes


@dataclass(frozen=True)
class StationPoint:
    id: str
    name: str
    line: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Network:
    """역 목록과 모든 역 쌍 소요시간(분). times가 비면 도보로만 계산한다(UC-15)."""

    stations: list[StationPoint] = field(default_factory=list)
    times: StationTimes = field(default_factory=StationTimes)


async def load_network(conn: asyncpg.Connection) -> Network:
    stations = [
        StationPoint(r["id"], r["name"], r["line"], r["lat"], r["lon"])
        for r in await conn.fetch("SELECT id, name, line, lat, lon FROM station ORDER BY id")
    ]
    return Network(stations, await station_times.load(conn))


async def get_station(conn: asyncpg.Connection, station_id: str) -> StationPoint | None:
    r = await conn.fetchrow(
        "SELECT id, name, line, lat, lon FROM station WHERE id = $1", station_id
    )
    return StationPoint(r["id"], r["name"], r["line"], r["lat"], r["lon"]) if r else None

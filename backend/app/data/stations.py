"""도시철도역·역간 소요시간 조회 (COMP-12, BR-12, FR-413). 코스 경로 계산이 쓴다."""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg


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
    times: dict[tuple[str, str], float] = field(default_factory=dict)


async def load_network(conn: asyncpg.Connection) -> Network:
    stations = [
        StationPoint(r["id"], r["name"], r["line"], r["lat"], r["lon"])
        for r in await conn.fetch("SELECT id, name, line, lat, lon FROM station ORDER BY id")
    ]
    times = {
        (r["from_id"], r["to_id"]): float(r["minutes"])
        for r in await conn.fetch("SELECT from_id, to_id, minutes FROM station_time")
    }
    return Network(stations, times)


async def get_station(conn: asyncpg.Connection, station_id: str) -> StationPoint | None:
    r = await conn.fetchrow(
        "SELECT id, name, line, lat, lon FROM station WHERE id = $1", station_id
    )
    return StationPoint(r["id"], r["name"], r["line"], r["lat"], r["lon"]) if r else None

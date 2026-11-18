"""역간 소요시간 조회 (COMP-12, FR-313, BR-12, NFR-001). 코스 계산(TravelTimeEstimator)이 쓴다.

station_time은 배치가 DATA-09로 미리 계산한 '모든 역 쌍의 최소 소요시간(분)'이다
(app/batch/station_times.py). 요청마다 경로를 찾지 않고 이 표를 한 번 읽어 찾으면 된다.

BR-12 연결 방법(TravelTimeEstimator.leg):
  도보 = 직선거리 × 1.3 ÷ 시속 4km. 도보 거리가 1km를 넘으면
  도시철도 = 출발 촬영지 station_walk_min
           + between(출발 촬영지 nearest_station_id, 도착 촬영지 nearest_station_id)
           + 도착 촬영지 station_walk_min
  과 비교해 짧은 쪽을 쓴다. 가까운 역·도보 분은 location에 이미 있다(FR-606).
between()이 None이면(표가 비었거나 표에 없는 역) 도보만 쓴다. 2026-11-18 원본은 역 127곳이
모두 이어진다(동해선·부산김해경전철과 환승 통로 포함).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg


@dataclass(frozen=True)
class StationTimes:
    minutes: dict[tuple[str, str], float] = field(default_factory=dict)

    def between(self, from_id: str | None, to_id: str | None) -> float | None:
        """두 역 사이 최소 분. 같은 역이면 0, 모르면 None."""
        if not from_id or not to_id:
            return None
        if from_id == to_id:
            return 0.0
        return self.minutes.get((from_id, to_id))

    def __len__(self) -> int:
        return len(self.minutes)


async def load(conn: asyncpg.Connection) -> StationTimes:
    """station_time 전체(역 127곳이면 약 1만 6천 줄). 앱이 한 번 읽어 두고 쓴다."""
    rows = await conn.fetch("SELECT from_id, to_id, minutes FROM station_time")
    return StationTimes({(r["from_id"], r["to_id"]): float(r["minutes"]) for r in rows})


async def between(conn: asyncpg.Connection, from_id: str, to_id: str) -> float | None:
    """한 쌍만 필요할 때."""
    if from_id == to_id:
        return 0.0
    v = await conn.fetchval(
        "SELECT minutes FROM station_time WHERE from_id = $1 AND to_id = $2", from_id, to_id
    )
    return float(v) if v is not None else None

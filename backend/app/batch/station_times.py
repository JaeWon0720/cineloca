"""역간 소요시간 전 쌍 계산 (BR-12, FR-413, SDD '역간 소요시간 사전 계산 station_time').

원본(부산교통공사 역간 거리 및 소요시간, 15158989)은 이웃한 역 사이 시간만 있다.
코스 경로 계산은 '출발역 → 도착역' 시간이 필요하므로 배치에서 모든 역 쌍의 최단 시간을 미리 구해
station_time에 넣는다. 원본 수집·적재는 C1 배치(R4)가 하고, 이 모듈은 계산과 반영만 한다.

- 같은 호선의 이웃 역: 원본 시간(양방향).
- 환승: 이름이 같고 호선이 다른 역(예: 서면 1호선·2호선)은 TRANSFER_MIN분으로 잇는다.
- 다익스트라를 역마다 돌린다(역 약 150곳 → 쌍 2만여 개, 1초 안).
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import asyncpg

TRANSFER_MIN = 5.0  # 환승 걷기·기다림. BR-12에 값이 없어 정한 값(SDD 정정 목록)


@dataclass(frozen=True)
class StationNode:
    id: str
    name: str
    line: str


@dataclass(frozen=True)
class Edge:
    """이웃한 두 역 사이 소요시간(분). 원본 한 줄."""

    from_id: str
    to_id: str
    minutes: float


def _base_name(name: str) -> str:
    """'서면역'·'서면(1호선)' → '서면'. 환승역을 이름으로 찾는다."""
    return re.sub(r"\(.*?\)|역$", "", name.strip()).strip()


def graph(
    stations: Sequence[StationNode], edges: Iterable[Edge], transfer_min: float = TRANSFER_MIN
) -> dict[str, list[tuple[str, float]]]:
    ids = {s.id for s in stations}
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges:
        if e.from_id in ids and e.to_id in ids and e.from_id != e.to_id and e.minutes > 0:
            adj[e.from_id].append((e.to_id, e.minutes))
            adj[e.to_id].append((e.from_id, e.minutes))
    by_name: dict[str, list[StationNode]] = defaultdict(list)
    for s in stations:
        by_name[_base_name(s.name)].append(s)
    for group in by_name.values():
        for a in group:
            for b in group:
                if a.id != b.id and a.line != b.line:
                    adj[a.id].append((b.id, transfer_min))
    return adj


def all_pairs(
    stations: Sequence[StationNode], edges: Iterable[Edge], transfer_min: float = TRANSFER_MIN
) -> dict[tuple[str, str], float]:
    """모든 (출발역, 도착역) 최단 시간(분, 소수 첫째 자리). 이어지지 않은 쌍은 빠진다."""
    adj = graph(stations, edges, transfer_min)
    out: dict[tuple[str, str], float] = {}
    for s in stations:
        dist = {s.id: 0.0}
        heap = [(0.0, s.id)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            for v, w in adj.get(u, ()):
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    heapq.heappush(heap, (nd, v))
        for t, d in dist.items():
            if t != s.id:
                out[(s.id, t)] = round(d, 1)
    return out


async def publish(conn: asyncpg.Connection, pairs: dict[tuple[str, str], float]) -> int:
    """station_time을 새 계산 결과로 바꾼다(한 트랜잭션). 반영한 쌍 수."""
    async with conn.transaction():
        await conn.execute("DELETE FROM station_time")
        await conn.copy_records_to_table(
            "station_time",
            records=[(a, b, m) for (a, b), m in pairs.items()],
            columns=["from_id", "to_id", "minutes"],
        )
    return len(pairs)

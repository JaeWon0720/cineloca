"""AI 맞춤 코스 후보 조회 (FR-309, COMP-12).

좌표가 있는 촬영지만(경로 계산 대상). 방문 인증 제외 장소도 코스에는 들어간다(화면이 표시, FR-506).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import asyncpg

from app.data import db


@dataclass(frozen=True)
class CandidateLocation:
    """점수화에 쓰는 촬영지 값. 좌표가 있는 촬영지만(경로 계산 대상, FR-210)."""

    location_id: int
    name: str
    gu: str | None
    lat: float
    lon: float
    tags: tuple[str, ...] = ()  # 승인된 태그(BR-04)
    genres: tuple[str, ...] = ()  # 촬영 작품들의 KMDb 장르
    work_titles: tuple[str, ...] = ()
    persons: tuple[str, ...] = ()  # 촬영 작품의 감독·배우
    work_count: int = 0
    embedding: tuple[float, ...] | None = None


Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]

# 작품·인물은 촬영 기록으로 이어진 것만. 임베딩은 문자열로 받아 파싱한다(pgvector 코덱 없이).
_CANDIDATES = """
SELECT l.id, l.name, l.gu, l.lat, l.lon, l.tags, l.embedding::text AS embedding,
       coalesce(array_agg(DISTINCT w.title) FILTER (WHERE w.id IS NOT NULL), '{}') AS titles,
       coalesce(array_agg(DISTINCT g) FILTER (WHERE g IS NOT NULL), '{}') AS genres,
       coalesce(array_agg(DISTINCT p.name) FILTER (WHERE p.id IS NOT NULL), '{}') AS persons,
       count(DISTINCT w.id) AS work_count
FROM location l
LEFT JOIN shooting s ON s.location_id = l.id
LEFT JOIN work w ON w.id = s.work_id
LEFT JOIN LATERAL unnest(w.genres) AS g ON TRUE
LEFT JOIN work_person wp ON wp.work_id = w.id
LEFT JOIN person p ON p.id = wp.person_id
WHERE l.lat IS NOT NULL
GROUP BY l.id
ORDER BY l.id
"""


def parse_vector(text: str | None) -> tuple[float, ...] | None:
    """'[0.1,0.2]'(pgvector)·'{0.1,0.2}'(배열) → 튜플."""
    if not text:
        return None
    body = text.strip().strip("[]{}")
    return tuple(float(x) for x in body.split(",")) if body else None


class PgCandidateSource:
    def __init__(self, connect: Connect = db.connection) -> None:
        self._connect = connect

    async def candidates(self) -> list[CandidateLocation]:
        async with self._connect() as conn:
            rows = await conn.fetch(_CANDIDATES)
        return [
            CandidateLocation(
                location_id=r["id"],
                name=r["name"],
                gu=r["gu"],
                lat=r["lat"],
                lon=r["lon"],
                tags=tuple(r["tags"]),
                genres=tuple(r["genres"]),
                work_titles=tuple(r["titles"]),
                persons=tuple(r["persons"]),
                work_count=r["work_count"],
                embedding=parse_vector(r["embedding"]),
            )
            for r in rows
        ]

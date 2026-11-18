"""F1 작품 아카이브 업무 로직 (COMP-03 ArchiveService, FR-101~107·802, API-001~003).

- 목록: 검색어를 배치와 같은 규칙(norm)으로 정규화해 작품명·감독·배우에서 찾는다(FR-101·102).
- 상세: DB에 있는 값만 돌려준다. KMDb 저장본(work.kmdb)에 없는 항목은 null·빈 목록이다(FR-103~106).
- 관람등급: KOBIS를 3초 제한으로 실시간 조회하고 저장하지 않는다. 코드가 없거나 실패·시간 초과면
  rating=null이라 상세 화면을 막지 않는다(FR-107, ERR-006).
- 출처 카드(FR-704): 작품·촬영 기록의 출처, KMDb 연결 작품이면 KMDb, 등급을 보여 주면 KOBIS.
- 결과는 API 응답 형식의 dict로 돌려준다(라우터가 응답 모델로 검증한다).
AI 작품 소개(API-004)는 work_intro.py가 맡는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import asyncpg

from app.core.config import get_settings
from app.core.names import norm  # work.title_norm을 만든 정규화 규칙(FR-602)
from app.core.places import DISTRICT_CENTERS
from app.data import db, locations
from app.data import works as repo
from app.data.kobis import KobisRatingProvider, RatingUnavailable

logger = logging.getLogger(__name__)

PAGE_SIZE = 20  # FR-101: 한 쪽에 20편
RELATED_LIMIT = 5  # FR-802
RATING_TIMEOUT_S = 3.0  # FR-107
KMDB_SOURCE = "DATA-04"
KOBIS_SOURCE = "DATA-05"

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]


class RatingProvider(Protocol):
    async def rating(self, movie_cd: str, timeout_s: float) -> str | None: ...


def default_rating_provider() -> RatingProvider | None:
    """KOBIS_API_KEY가 있을 때만 실제 KOBIS를 부른다. 없으면 등급은 늘 null."""
    key = get_settings().kobis_api_key
    return KobisRatingProvider(key) if key else None


def _summary(w: repo.WorkRow) -> dict[str, Any]:
    return {
        "id": w.id,
        "title": w.title,
        "release_year": w.release_year,
        "genres": w.genres,
        "director": w.director,
        "location_count": w.location_count,
        "kmdb_matched": w.kmdb_matched,
    }


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


async def _cards(conn: asyncpg.Connection, ids: Iterable[str]) -> list[dict[str, Any]]:
    rows = await locations.sources(conn, sorted(set(ids)))  # 출처 대장 조회는 F2와 같이 쓴다
    return [
        {"id": s.id, "provider": s.org, "name": s.name, "base_date": s.base_date, "url": s.url}
        for s in rows
    ]


class ArchiveService:
    def __init__(
        self, connect: Connect = db.connection, ratings: RatingProvider | None = None
    ) -> None:
        self._connect = connect
        self._ratings = ratings

    async def list_works(
        self,
        *,
        q: str | None = None,
        genre: str | None = None,
        sort: str = "title",
        page: int = 1,
        size: int = PAGE_SIZE,
    ) -> dict[str, Any]:
        """API-001. genre는 KMDb 장르 이름 그대로(응답의 genres 값) 받는다."""
        query = repo.WorkQuery(
            q=norm(q) or None, genre=genre or None, sort=sort, page=page, size=size
        )
        async with self._connect() as conn:
            rows = await repo.page(conn, query)
            source_ids = {w.source_id for w in rows.items}
            if any(w.kmdb_matched for w in rows.items):
                source_ids.add(KMDB_SOURCE)
            cards = await _cards(conn, source_ids)
        return {
            "total": rows.total,
            "page": page,
            "size": size,
            "items": [_summary(w) for w in rows.items],
            "sources": cards,
        }

    async def get_work(self, work_id: int) -> dict[str, Any] | None:
        """API-002. 없는 id면 None. KMDb 미매칭 작품은 작품명과 촬영지만 채워진다(FR-604)."""
        async with self._connect() as conn:
            row = await repo.get(conn, work_id)
            if row is None:
                return None
            related = await repo.similar(conn, work_id, RELATED_LIMIT)
            kmdb = row.kmdb or {}
            source_ids = set(row.source_ids)
            if row.work.kmdb_matched:
                source_ids.add(kmdb.get("source_id") or KMDB_SOURCE)
            cards = await _cards(conn, source_ids)
        return {
            **_summary(row.work),
            "cast": row.cast,
            "synopsis": _text(kmdb.get("plot")),
            # 포스터·스틸은 팀 결정으로 보여 주지 않고 KMDb 원문 링크(kmdb_url)로 안내한다(#12).
            # 예고편 주소는 아직 배치가 저장하지 않는다(TBD-01)
            "trailer_url": None,
            "kmdb_url": _text(kmdb.get("link")),
            "locations": [
                {
                    "id": loc.id,
                    "name": loc.name,
                    "gu": loc.gu if loc.gu in DISTRICT_CENTERS else None,  # BR-15
                    "located": loc.lat is not None and loc.lon is not None,
                    "lat": loc.lat,
                    "lon": loc.lon,
                }
                for loc in row.locations
            ],
            "related": [
                {"id": w.id, "title": w.title, "release_year": w.release_year} for w in related
            ],
            "sources": cards,
        }

    async def get_rating(self, work_id: int) -> dict[str, Any] | None:
        """API-003. 없는 id면 None. KOBIS 응답은 등급 문자열만 쓰고 어디에도 남기지 않는다."""
        async with self._connect() as conn:
            found, movie_cd = await repo.kobis_code(conn, work_id)
        if not found:
            return None
        rating = await self._live_rating(movie_cd) if movie_cd and self._ratings else None
        if rating is None:
            return {"rating": None, "sources": []}
        async with self._connect() as conn:
            return {"rating": rating, "sources": await _cards(conn, [KOBIS_SOURCE])}

    async def _live_rating(self, movie_cd: str) -> str | None:
        assert self._ratings is not None
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._ratings.rating(movie_cd, RATING_TIMEOUT_S), timeout=RATING_TIMEOUT_S
            )
        except (RatingUnavailable, asyncio.TimeoutError) as e:
            # ERR-006: 공급자·오류 종류·걸린 시간만 남긴다(응답 본문·키·URL은 남기지 않는다)
            latency_ms = round((time.monotonic() - started) * 1000)
            logger.warning(
                "KOBIS 등급 조회 실패 provider=kobis error=%s latency_ms=%d",
                type(e).__name__,
                latency_ms,
            )
            return None

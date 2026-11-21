"""출처 카드 조회 (FR-704, BR-02). 출처 ID를 출처 대장(source)의 제공기관·데이터명·링크로 바꾼다.

F3 대화형 탐색·필터 패널 검색 응답의 sources에 쓴다. 결과는 SourceCard 형식의 dict다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from typing import Any

import asyncpg

from app.data import db, locations

SourceCards = Callable[[Iterable[str]], Awaitable[list[dict[str, Any]]]]


Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]


async def source_cards(
    ids: Iterable[str], *, connect: Connect = db.connection
) -> list[dict[str, Any]]:
    async with connect() as conn:
        rows = await locations.sources(conn, sorted(set(ids)))
    return [
        {"id": s.id, "provider": s.org, "name": s.name, "base_date": s.base_date, "url": s.url}
        for s in rows
    ]

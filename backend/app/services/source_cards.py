"""출처 카드와 출처 대장 (FR-704·705, BR-02).

- source_cards: 출처 ID를 출처 대장(source)의 제공기관·데이터명·링크로 바꾼다. F3 대화형 탐색·
  필터 패널 검색 응답의 sources에 쓴다. 결과는 SourceCard 형식의 dict다.
- ledger: UI-09 데이터 출처 화면(API-019)의 출처 대장 전체.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from typing import Any

import asyncpg

from app.core.sources import RUNTIME_SOURCES
from app.data import db, locations, sources

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


async def ledger(*, connect: Connect = db.connection) -> list[dict[str, Any]]:
    """UI-09 출처 대장: 저장하는 출처(source 테이블)에 요청 때만 쓰는 출처(NO_STORE)를 붙인다.

    카드 필드(id·provider·name·base_date·url)는 source_cards와 같고 이용조건·저장 정책을 더한다.
    """
    async with connect() as conn:
        rows = await sources.ledger(conn)
    stored = [
        {
            "id": s.id,
            "provider": s.org,
            "name": s.name,
            "base_date": s.base_date,
            "url": s.url,
            "license": s.license,
            "policy": s.policy,
        }
        for s in rows
    ]
    ids = {s["id"] for s in stored}
    return stored + [dict(s) for s in RUNTIME_SOURCES if s["id"] not in ids]

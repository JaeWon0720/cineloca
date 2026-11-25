"""출처 대장(source 테이블) 읽기 (FR-705, API-019)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import asyncpg


@dataclass(frozen=True)
class LedgerRow:
    id: str
    org: str
    name: str
    url: str
    license: str
    policy: str
    base_date: date | None


async def ledger(conn: asyncpg.Connection) -> list[LedgerRow]:
    rows = await conn.fetch(
        "SELECT id, org, name, url, license, policy, base_date FROM source ORDER BY id"
    )
    return [LedgerRow(**dict(r)) for r in rows]

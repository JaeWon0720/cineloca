"""review_item(검수 큐) 쓰기 (FR-608, BR-04).

배치는 API 서버와 다른 프로세스라 연결을 직접 넘겨받는다(asyncpg.Connection).
제안은 'pending'으로만 넣는다. 승인·반려(FR-609)와 서비스 반영은 관리자 기능이 맡는다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, Literal, Protocol

import asyncpg

# 같은 대상에 같은 제안자의 미검수 항목이 이미 있으면 넣지 않는다(동기화를 다시 돌려도 중복 없음).
_INSERT = """
INSERT INTO review_item (kind, target_type, target_id, proposal, proposer, score)
SELECT $1::text, $2::text, $3::integer, $4::jsonb, $5::text, $6::numeric
WHERE NOT EXISTS (
    SELECT 1 FROM review_item
    WHERE kind = $1::text AND target_type = $2::text AND target_id = $3::integer
      AND proposer = $5::text AND status = 'pending'
)
"""


class ProposalRow(Protocol):
    """검수 항목 한 행. app.batch.ai_review.ReviewProposal이 이 모양이다."""

    @property
    def kind(self) -> str: ...
    @property
    def target_type(self) -> str: ...
    @property
    def target_id(self) -> int: ...
    @property
    def proposal(self) -> Mapping[str, Any]: ...
    @property
    def proposer(self) -> str: ...
    @property
    def score(self) -> float | None: ...


async def add_proposals(conn: asyncpg.Connection, rows: Iterable[ProposalRow]) -> int:
    """미검수 항목으로 넣고, 실제로 넣은 수를 돌려준다. 한 트랜잭션으로 넣는다."""
    inserted = 0
    async with conn.transaction():
        for row in rows:
            score = None if row.score is None else Decimal(str(row.score))
            status = await conn.execute(
                _INSERT,
                row.kind,
                row.target_type,
                row.target_id,
                json.dumps(dict(row.proposal), ensure_ascii=False),
                row.proposer,
                score,
            )
            inserted += int(status.rsplit(" ", 1)[-1])  # "INSERT 0 1" → 1
    return inserted


async def pending_targets(
    conn: asyncpg.Connection, kind: Literal["tag", "match"], proposer: Literal["rule", "ai"] = "ai"
) -> set[int]:
    """이미 미검수 제안이 있는 대상 id. 배치는 이 대상을 AI에 다시 묻지 않는다(호출 절약)."""
    records = await conn.fetch(
        "SELECT DISTINCT target_id FROM review_item "
        "WHERE kind = $1::text AND proposer = $2::text AND status = 'pending'",
        kind,
        proposer,
    )
    return {r["target_id"] for r in records}

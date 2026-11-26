"""촬영지 규칙 태그 반영 (FR-607). 키워드 규칙은 app/core/place_tags.py rule_tags.

태그가 하나도 없는 촬영지에만 붙인다. 관리자가 고르거나 승인한 태그(FR-609·610)가 있으면
그대로 두므로, 동기화를 다시 돌려도 관리자가 고친 태그를 덮지 않는다.
규칙으로 못 붙인 곳은 비어 있고, AI 태그 제안(FR-608, batch/ai_review.py)의 대상이 된다.
관람 주의 구분(caution, FR-209)도 비어 있는 촬영지에만 장소명 규칙(rule_caution)으로 붙인다.
값이 붙으면 상세 화면에 '외부에서만 관람' 안내가 나오고 방문 인증에서 빠진다(BR-06).

동기화 반영(staging.publish)이 같은 트랜잭션에서 부르고, 동기화 없이 지금 DB에만 붙일 때는
python -m app.batch --tags-only.
"""

from __future__ import annotations

import asyncpg

from app.core.place_tags import rule_caution, rule_tags


async def apply(conn: asyncpg.Connection) -> dict[str, int]:
    """태그 없는 촬영지 수(checked)와 이번에 태그를 붙인 수(tagged)를 돌려준다."""
    rows = await conn.fetch("SELECT id, name FROM location WHERE cardinality(tags) = 0")
    updates = [(r["id"], tags) for r in rows if (tags := rule_tags(r["name"]))]
    if updates:
        await conn.executemany(
            "UPDATE location SET tags = $2::text[] WHERE id = $1 AND cardinality(tags) = 0",
            updates,
        )
    cautions = await _apply_caution(conn)
    return {"checked": len(rows), "tagged": len(updates), "caution": cautions}


async def _apply_caution(conn: asyncpg.Connection) -> int:
    """관람 주의 구분이 빈 촬영지에 장소명 규칙으로 붙이고 붙인 수를 돌려준다(FR-209, BR-06).

    이미 값이 있으면(관리자가 FR-610으로 정한 값 포함) 그대로 둔다.
    """
    rows = await conn.fetch("SELECT id, name FROM location WHERE caution IS NULL")
    updates = [(r["id"], c) for r in rows if (c := rule_caution(r["name"]))]
    if updates:
        await conn.executemany(
            "UPDATE location SET caution = $2 WHERE id = $1 AND caution IS NULL", updates
        )
    return len(updates)

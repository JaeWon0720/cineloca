"""검수 큐 조회·처리와 촬영지 속성 편집 (API-022·023, FR-609·610, BR-04·06·07).

관리 API가 쓴다. 검수 큐에 넣는 쪽(배치)은 review_queue.py에 있다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import asyncpg

ListStatus = Literal["pending", "handled", "all"]

_TARGET = """
    CASE WHEN r.target_type = 'work' THEN w.title ELSE l.name END AS target_name,
    CASE WHEN r.target_type = 'work' THEN w.release_year END AS target_year,
    (CASE WHEN r.target_type = 'work' THEN w.id ELSE l.id END) IS NOT NULL AS target_exists
"""
_FROM = """
    FROM review_item r
    LEFT JOIN work w ON r.target_type = 'work' AND w.id = r.target_id
    LEFT JOIN location l ON r.target_type = 'location' AND l.id = r.target_id
"""
_COLUMNS = (
    "r.id, r.kind, r.target_type, r.target_id, r.proposer, r.score, r.status, r.proposal,"
    " r.created_at, r.handled_at," + _TARGET
)


@dataclass(frozen=True)
class ReviewRow:
    id: int
    kind: str
    target_type: str
    target_id: int
    target_name: str | None  # 대상이 동기화로 지워졌으면 None
    target_year: int | None
    target_exists: bool
    proposer: str
    score: float | None
    status: str
    proposal: dict[str, Any]
    created_at: datetime
    handled_at: datetime | None


def _row(r: asyncpg.Record) -> ReviewRow:
    proposal = r["proposal"]
    return ReviewRow(
        id=r["id"],
        kind=r["kind"],
        target_type=r["target_type"],
        target_id=r["target_id"],
        target_name=r["target_name"],
        target_year=r["target_year"],
        target_exists=r["target_exists"],
        proposer=r["proposer"],
        score=None if r["score"] is None else float(r["score"]),
        status=r["status"],
        proposal=json.loads(proposal) if isinstance(proposal, str) else dict(proposal),
        created_at=r["created_at"],
        handled_at=r["handled_at"],
    )


def _status_sql(status: ListStatus) -> str:
    return {
        "pending": "r.status = 'pending'",
        "handled": "r.status <> 'pending'",
        "all": "TRUE",
    }[status]


async def list_reviews(
    conn: asyncpg.Connection, status: ListStatus, kind: str | None, page: int, size: int
) -> tuple[int, list[ReviewRow]]:
    """대기 목록은 오래된 것부터, 처리 목록은 최근 처리한 것부터."""
    where = f"WHERE {_status_sql(status)} AND ($1::text IS NULL OR r.kind = $1::text)"
    order = "r.handled_at DESC, r.id DESC" if status == "handled" else "r.id"
    total = await conn.fetchval(f"SELECT count(*) FROM review_item r {where}", kind)
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} {_FROM} {where} ORDER BY {order} LIMIT $2 OFFSET $3",
        kind,
        size,
        (page - 1) * size,
    )
    return total, [_row(r) for r in rows]


async def counts(conn: asyncpg.Connection) -> tuple[int, int]:
    r = await conn.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'pending') AS pending,"
        " count(*) FILTER (WHERE status <> 'pending') AS handled FROM review_item"
    )
    return r["pending"], r["handled"]


async def get_for_update(conn: asyncpg.Connection, review_id: int) -> ReviewRow | None:
    """처리할 항목을 잠그고 읽는다(두 관리자 창이 같은 항목을 동시에 처리하지 않게)."""
    r = await conn.fetchrow(f"SELECT {_COLUMNS} {_FROM} WHERE r.id = $1 FOR UPDATE OF r", review_id)
    return _row(r) if r else None


async def get(conn: asyncpg.Connection, review_id: int) -> ReviewRow | None:
    r = await conn.fetchrow(f"SELECT {_COLUMNS} {_FROM} WHERE r.id = $1", review_id)
    return _row(r) if r else None


async def mark_handled(
    conn: asyncpg.Connection,
    review_id: int,
    status: str,
    proposal: dict[str, Any],
    handled_by: str,
) -> None:
    await conn.execute(
        "UPDATE review_item SET status = $2, proposal = $3::jsonb, handled_by = $4,"
        " handled_at = clock_timestamp() WHERE id = $1",
        review_id,
        status,
        json.dumps(proposal, ensure_ascii=False),
        handled_by,
    )


async def add_location_tags(conn: asyncpg.Connection, location_id: int, tags: list[str]) -> None:
    """승인된 태그를 촬영지에 더한다. 있던 태그는 두고 없는 것만 뒤에 붙인다(BR-04)."""
    await conn.execute(
        "UPDATE location SET tags = tags || ARRAY(SELECT t FROM unnest($2::text[])"
        " WITH ORDINALITY AS x(t, n) WHERE NOT t = ANY(tags) ORDER BY n) WHERE id = $1",
        location_id,
        tags,
    )


# ── API-023 촬영지 속성 ────────────────────────────────────
@dataclass(frozen=True)
class LocationAttrs:
    id: int
    name: str
    gu: str | None
    address: str | None
    located: bool
    tags: list[str]
    caution: str | None
    safety_note: str | None
    cert_radius_m: int
    cert_excluded: bool


EDITABLE = ("tags", "caution", "safety_note", "cert_radius_m", "cert_excluded")
_ATTRS = (
    "id, name, gu, address, lat IS NOT NULL AS located, tags, caution, safety_note,"
    " cert_radius_m, cert_excluded"
)


def _attrs(r: asyncpg.Record) -> LocationAttrs:
    return LocationAttrs(**{**dict(r), "tags": list(r["tags"])})


async def location_attrs(conn: asyncpg.Connection, location_id: int) -> LocationAttrs | None:
    r = await conn.fetchrow(f"SELECT {_ATTRS} FROM location WHERE id = $1", location_id)
    return _attrs(r) if r else None


async def update_location(
    conn: asyncpg.Connection, location_id: int, values: dict[str, Any]
) -> LocationAttrs | None:
    """고칠 열만 바꾼다. 열 이름은 EDITABLE 안에서만 고르고 값은 바인딩 매개변수로 넣는다."""
    cols = [c for c in EDITABLE if c in values]
    if not cols:
        return await location_attrs(conn, location_id)
    sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(cols, start=2))
    r = await conn.fetchrow(
        f"UPDATE location SET {sets} WHERE id = $1 RETURNING {_ATTRS}",
        location_id,
        *(values[c] for c in cols),
    )
    return _attrs(r) if r else None

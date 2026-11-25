"""검수 큐 처리와 촬영지 속성 편집 (API-022·023, FR-609·610, BR-04·06·07).

검수 항목 처리(decide)는 DB 없이 판단하고, 서비스가 한 트랜잭션으로 기록·반영한다.
- 태그(tag, 대상 촬영지): 승인·수정하면 그 태그를 촬영지에 바로 더한다
  (승인된 태그만 서비스에 보임, BR-04).
- 매칭(match, 대상 작품): 승인·수정·반려 결과는 다음 동기화가 읽어 작품과 KMDb를 잇거나 끊는다
  (staging.review_decisions). KMDb 본문(줄거리 등)은 배치만 받아 오므로 API가 work를 직접
  고치지 않는다. 관리자 화면의 '지금 동기화'(API-021)로 바로 반영할 수 있다.
  AI 판정(proposer=ai)은 승인·수정했을 때만 결정이 된다.
  not_listed를 승인하면 'KMDb에 없음'(연결 안 함)이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal, get_args

import asyncpg

from app.core.place_tags import PlaceType
from app.data import db
from app.data import reviews as repo

Connect = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]
Action = Literal["approve", "modify", "reject"]

HANDLED_BY = "admin"  # 관리자 계정은 하나다(FR-615)
PLACE_TAGS: frozenset[str] = frozenset(get_args(PlaceType))
KMDB_ID = re.compile(r"[A-Z][0-9]{5}")  # KMDb DOCID: 영문 1자 + 숫자 5자리(예: K24589, F11599)
CERT_RADIUS_RANGE = (50, 300)  # BR-07
SAFETY_NOTE_MAX = 200
_STATUS = {"approve": "approved", "modify": "modified", "reject": "rejected"}


class ReviewError(Exception):
    """처리할 수 없는 요청. code는 API 오류 코드다."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _bad(message: str) -> ReviewError:
    return ReviewError(400, "INVALID_REQUEST", message)


@dataclass(frozen=True)
class Decision:
    status: str
    proposal: dict[str, Any]
    add_tags: list[str]  # 촬영지에 바로 더할 태그(태그 항목 승인·수정)


def check_tags(tags: Sequence[str]) -> list[str]:
    unknown = [t for t in tags if t not in PLACE_TAGS]
    if unknown:
        raise _bad("태그는 장소 유형 목록에서 골라 주세요.")
    return list(dict.fromkeys(tags))


def decide(
    item: repo.ReviewRow,
    action: Action,
    tags: Sequence[str] | None = None,
    kmdb_id: str | None = None,
) -> Decision:
    """검수 항목 하나를 어떻게 처리할지 정한다(FR-609). DB를 바꾸지 않는다."""
    if item.status != "pending":
        raise ReviewError(409, "REVIEW_ALREADY_HANDLED", "이미 처리된 항목입니다.")
    status = _STATUS[action]
    if action == "reject":
        return Decision(status, item.proposal, [])
    if not item.target_exists:
        raise ReviewError(
            409, "REVIEW_TARGET_GONE", "대상이 동기화로 없어져 승인할 수 없습니다. 반려해 주세요."
        )
    proposal = dict(item.proposal)
    if item.kind == "tag":
        if action == "modify":
            if not tags:
                raise _bad("고칠 태그를 하나 이상 골라 주세요.")
            proposal["tags"] = check_tags(tags)
        chosen = check_tags(proposal.get("tags") or [])
        if not chosen:
            raise _bad("제안된 태그가 없어 승인할 수 없습니다. 수정으로 태그를 골라 주세요.")
        return Decision(status, proposal, chosen)
    # match
    if action == "modify":
        if not kmdb_id or not KMDB_ID.fullmatch(kmdb_id):
            raise _bad("KMDb 번호는 영문 1자와 숫자 5자리예요(예: K24589).")
        proposal["kmdb_id"] = kmdb_id
    elif item.proposer == "ai" and proposal.get("verdict") == "unsure":
        raise _bad("AI가 판단하지 못한 항목이라 승인할 수 없습니다. 수정이나 반려를 골라 주세요.")
    elif item.proposer == "rule" and not proposal.get("kmdb_id"):
        raise _bad("후보 KMDb 번호가 없어 승인할 수 없습니다. 수정이나 반려를 골라 주세요.")
    return Decision(status, proposal, [])


def check_location_patch(values: dict[str, Any]) -> dict[str, Any]:
    """API-023 값 범위 검사. 스키마가 형식을 보고, 여기서는 업무 규칙을 본다."""
    out = dict(values)
    if "tags" in out:
        if out["tags"] is None:
            raise _bad("태그 목록은 비울 수는 있어도 null일 수 없습니다.")
        out["tags"] = check_tags(out["tags"])
    if "cert_radius_m" in out:
        lo, hi = CERT_RADIUS_RANGE
        r = out["cert_radius_m"]
        if r is None or not lo <= r <= hi:
            raise _bad(f"인증 반경은 {lo}~{hi}m 사이 정수예요.")
    if "cert_excluded" in out and out["cert_excluded"] is None:
        raise _bad("인증 제외 여부를 골라 주세요.")
    if "safety_note" in out:
        note = (out["safety_note"] or "").strip()
        if len(note) > SAFETY_NOTE_MAX:
            raise _bad(f"안전 안내는 {SAFETY_NOTE_MAX}자까지예요.")
        out["safety_note"] = note or None
    return out


class AdminReviewService:
    def __init__(self, connect: Connect = db.connection) -> None:
        self._connect = connect

    async def list(
        self, status: repo.ListStatus, kind: str | None, page: int, size: int
    ) -> dict[str, Any]:
        async with self._connect() as conn:
            total, rows = await repo.list_reviews(conn, status, kind, page, size)
            pending, handled = await repo.counts(conn)
        return {
            "total": total,
            "page": page,
            "size": size,
            "counts": {"pending": pending, "handled": handled},
            "items": rows,
        }

    async def handle(
        self,
        review_id: int,
        action: Action,
        tags: Sequence[str] | None = None,
        kmdb_id: str | None = None,
    ) -> repo.ReviewRow:
        async with self._connect() as conn, conn.transaction():
            item = await repo.get_for_update(conn, review_id)
            if item is None:
                raise ReviewError(404, "REVIEW_NOT_FOUND", "검수 항목을 찾지 못했습니다.")
            d = decide(item, action, tags, kmdb_id)
            await repo.mark_handled(conn, review_id, d.status, d.proposal, HANDLED_BY)
            if d.add_tags:
                await repo.add_location_tags(conn, item.target_id, d.add_tags)
            done = await repo.get(conn, review_id)
        assert done is not None
        return done

    async def location(self, location_id: int) -> repo.LocationAttrs:
        async with self._connect() as conn:
            attrs = await repo.location_attrs(conn, location_id)
        if attrs is None:
            raise ReviewError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다.")
        return attrs

    async def update_location(self, location_id: int, values: dict[str, Any]) -> repo.LocationAttrs:
        checked = check_location_patch(values)
        async with self._connect() as conn:
            attrs = await repo.update_location(conn, location_id, checked)
        if attrs is None:
            raise ReviewError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다.")
        return attrs

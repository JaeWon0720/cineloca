"""검수 큐·촬영지 속성 DB 테스트 (API-022·023, FR-609·610, BR-04). 테스트 전용 DB가 있을 때만 돈다.
한 트랜잭션 안에서 표를 비우고 시험 데이터를 넣은 뒤 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_admin_review_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.data import staging
from app.services.admin_review import AdminReviewService, ReviewError

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 테스트 전용 가짜 데이터
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '시험', '영화 속 부산정보', 'https://example.org/1', '시험', 'STORE');
INSERT INTO work (id, title, title_norm, release_year, source_id) VALUES
 (12, '대외비', '대외비', 2023, 'DATA-01'), (57, '신과함께', '신과함께', 2017, 'DATA-01');
INSERT INTO location (id, name, name_norm, gu, lat, lon, tags) VALUES
 (5, '온천천', '온천천', '동래구', 35.2, 129.08, '{거리·도로}'),
 (6, '충무동 새벽시장', '충무동새벽시장', '서구', NULL, NULL, '{}');
INSERT INTO review_item (id, kind, target_type, target_id, proposal, proposer, score) VALUES
 (1, 'match', 'work', 12, '{"kmdb_id": "K24589", "kmdb_title": "대외비"}', 'rule', 0.659),
 (2, 'match', 'work', 57, '{"kmdb_id": "K31342", "kmdb_title": "신과함께(금연편)"}', 'rule', 0.71),
 (3, 'match', 'work', 57, '{"verdict": "not_listed", "kmdb_id": null}', 'ai', NULL),
 (4, 'tag', 'location', 5, '{"tags": ["공원·산", "거리·도로"], "reason": "하천"}', 'ai', NULL),
 (5, 'tag', 'location', 99, '{"tags": ["시장"]}', 'ai', NULL);
"""


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[AdminReviewService, asyncpg.Connection], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "TRUNCATE review_item, course_stop, course, photo, shooting, work_person,"
                " work, location, source RESTART IDENTITY CASCADE"
            )
            await conn.execute(FIXTURE)
            await body(AdminReviewService(lambda: _borrow(conn)), conn)
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


def test_list_pending_and_handled_with_target_names() -> None:
    async def body(s: AdminReviewService, conn: asyncpg.Connection) -> None:
        r = await s.list("pending", None, 1, 20)
        assert r["total"] == 5 and r["counts"] == {"pending": 5, "handled": 0}
        names = {i.id: (i.target_name, i.target_year, i.target_exists) for i in r["items"]}
        assert names[1] == ("대외비", 2023, True) and names[4] == ("온천천", None, True)
        assert names[5] == (None, None, False)  # 동기화로 없어진 촬영지
        assert r["items"][0].proposal["kmdb_id"] == "K24589" and r["items"][0].score == 0.659

        page = await s.list("pending", "match", 2, 2)
        assert page["total"] == 3 and [i.id for i in page["items"]] == [3]

        await s.handle(2, "reject")
        await s.handle(1, "approve")
        handled = await s.list("handled", None, 1, 20)
        assert [i.id for i in handled["items"]] == [1, 2]  # 최근 처리 먼저
        assert handled["counts"] == {"pending": 3, "handled": 2}

    run(body)


def test_tag_approval_adds_only_new_tags() -> None:
    async def body(s: AdminReviewService, conn: asyncpg.Connection) -> None:
        done = await s.handle(4, "approve")
        assert done.status == "approved" and done.handled_at is not None
        tags = await conn.fetchval("SELECT tags FROM location WHERE id = 5")
        assert tags == ["거리·도로", "공원·산"]  # BR-04: 승인된 태그만, 중복 없이
        assert await conn.fetchval("SELECT handled_by FROM review_item WHERE id = 4") == "admin"
        with pytest.raises(ReviewError) as e:
            await s.handle(4, "reject")
        assert e.value.code == "REVIEW_ALREADY_HANDLED"
        with pytest.raises(ReviewError) as e:
            await s.handle(5, "approve")
        assert e.value.code == "REVIEW_TARGET_GONE"
        with pytest.raises(ReviewError) as e:
            await s.handle(77, "approve")
        assert e.value.code == "REVIEW_NOT_FOUND"

    run(body)


def test_match_handling_feeds_next_sync() -> None:
    """매칭 처리는 work를 바로 고치지 않고, 다음 동기화가 읽는 결정이 된다."""

    async def body(s: AdminReviewService, conn: asyncpg.Connection) -> None:
        done = await s.handle(1, "modify", kmdb_id="F11599")
        assert done.proposal == {"kmdb_id": "F11599", "kmdb_title": "대외비"}
        await s.handle(3, "approve")  # AI 'KMDb에 없음'에 동의
        assert await staging.review_decisions(conn) == {
            "대외비": ("modified", "F11599"),
            "신과함께": ("rejected", None),
        }
        assert await conn.fetchval("SELECT kmdb_id FROM work WHERE id = 12") is None

    run(body)


def test_location_attrs_edit() -> None:
    async def body(s: AdminReviewService, conn: asyncpg.Connection) -> None:
        a = await s.location(6)
        assert (a.located, a.cert_radius_m, a.cert_excluded, a.tags) == (False, 100, False, [])

        a = await s.update_location(
            5, {"caution": "residential", "safety_note": " 주민 통행로 ", "cert_radius_m": 50}
        )
        assert (a.caution, a.safety_note, a.cert_radius_m, a.tags) == (
            "residential", "주민 통행로", 50, ["거리·도로"]
        )  # fmt: skip
        a = await s.update_location(5, {"caution": None, "cert_excluded": True, "tags": ["해변"]})
        assert (a.caution, a.safety_note, a.cert_excluded, a.tags) == (
            None, "주민 통행로", True, ["해변"]
        )  # fmt: skip
        assert (await s.update_location(5, {})).cert_radius_m == 50
        with pytest.raises(ReviewError) as e:
            await s.update_location(999, {"cert_excluded": True})
        assert e.value.code == "LOCATION_NOT_FOUND"

    run(body)

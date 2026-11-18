"""F5 DB 테스트: 인증 대상·스탬프북·추천 코스 조회 (COMP-12, FR-504~508).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고
시험 데이터를 넣은 뒤 끝나면 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_stamps_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.services.stamp_service import CourseNotFound, GeoFix, StampService
from app.support.tokens import TokenSigner

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

SIGNER = TokenSigner("d" * 40)

FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '부산영상위원회', '영화 속 부산정보', 'https://example.org/1', '제한 없음', 'STORE');
INSERT INTO work (id, title, title_norm, release_year, source_id) VALUES
 (1, '옛영화', '옛영화', 2001, 'DATA-01'), (2, '새영화', '새영화', 2020, 'DATA-01'),
 (3, '중간영화', '중간영화', 2010, 'DATA-01'), (4, '연도없음', '연도없음', NULL, 'DATA-01');
INSERT INTO location (id, name, name_norm, gu, lat, lon, caution, cert_radius_m, cert_excluded)
VALUES
 (1, '해변', '해변', '해운대구', 35.1, 129.0, NULL, 100, FALSE),
 (2, '시장', '시장', '중구', 35.2, 129.1, NULL, 200, FALSE),
 (3, '골목집', '골목집', '동구', 35.3, 129.2, 'residential', 100, FALSE),
 (4, '미확인', '미확인', '서구', NULL, NULL, NULL, 100, FALSE);
INSERT INTO shooting (work_id, location_id, source_id) VALUES
 (1, 1, 'DATA-01'), (2, 1, 'DATA-01'), (3, 1, 'DATA-01'), (4, 1, 'DATA-01'), (1, 2, 'DATA-01');
INSERT INTO course (id, theme, title, status) VALUES
 (1, 'district', '승인 코스', 'approved'), (2, 'district', '초안 코스', 'draft');
INSERT INTO course_stop (course_id, ord, location_id) VALUES
 (1, 2, 2), (1, 1, 1), (1, 3, 3), (2, 1, 1), (2, 2, 2);
"""


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[StampService], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "TRUNCATE review_item, course_stop, course, photo, shooting, work_person, person,"
                " work, location, station_time, station, source RESTART IDENTITY CASCADE"
            )
            await conn.execute(FIXTURE)
            await body(StampService(SIGNER, connect=lambda: _borrow(conn)))
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


def test_certify_reads_radius_and_exclusion_from_db() -> None:
    async def body(s: StampService) -> None:
        near_market = GeoFix(35.2 + 150 / 111_195, 129.1, 10)  # 시장 반경 200m 안
        ok = await s.certify(2, near_market, [])

        assert ok.ok and ok.distance_m == 150

    run(body)


def test_stamp_book_shows_recent_three_works() -> None:
    async def body(s: StampService) -> None:
        book = await s.verify([SIGNER.sign_stamp(1), SIGNER.sign_stamp(2)])

        beach = book["valid"][0]
        assert beach["location"] == {"id": 1, "name": "해변"}
        assert [w["title"] for w in beach["works"]] == ["새영화", "중간영화", "옛영화"]
        assert beach["work_count"] == 4  # 나머지는 '외 N편'
        assert book["unavailable_count"] == 0

    run(body)


def test_badge_uses_only_approved_course_in_stop_order() -> None:
    async def body(s: StampService) -> None:
        partial = await s.issue_badge([SIGNER.sign_stamp(2)], course_id=1)
        done = await s.issue_badge([SIGNER.sign_stamp(1), SIGNER.sign_stamp(2)], course_id=1)
        draft = s.issue_badge([], course_id=2)
        book = await s.verify([done.token])  # type: ignore[list-item]

        assert partial.missing == [1]  # 골목집(주거지)은 대상 아님, 순서는 ord 기준
        assert done.ok and done.course_key == "course:1"
        assert book["badges"][0]["course_title"] == "승인 코스"
        with pytest.raises(CourseNotFound):
            await draft  # 승인 전 코스는 배지 대상 아님

    run(body)

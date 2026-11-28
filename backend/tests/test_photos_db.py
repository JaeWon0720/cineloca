"""장소 사진 반영 DB 테스트 (FR-207, BR-03). 스키마(db/init)를 적재한 '테스트 전용' DB에서만 돈다.
CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_photos_db.py
"""

import asyncio
import os

import asyncpg
import pytest

from app.data import photos

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")


def test_replace_twice_keeps_one_row_per_url_and_other_sources() -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("TRUNCATE shooting, work, location RESTART IDENTITY CASCADE")
            loc = await conn.fetchval(
                "INSERT INTO location (name, name_norm, lat, lon) VALUES ('가', '가', 35.1, 129.0)"
                " RETURNING id"
            )
            await conn.execute(  # 다른 출처의 사진은 건드리지 않는다
                "INSERT INTO photo (location_id, url, kogl_type, title, source_id)"
                " VALUES ($1, 'https://x/keep.jpg', 1, '남김', 'DATA-01')",
                loc,
            )
            rows = [(loc, "https://x/a.jpg", "가"), (loc, "https://x/a.jpg", "가")]
            assert await photos.replace(conn, rows) == 1
            assert await photos.replace(conn, rows) == 1  # 다시 돌려도 중복 없음
            got = await conn.fetch("SELECT url, source_id, kogl_type FROM photo ORDER BY url")
            assert [tuple(r) for r in got] == [
                ("https://x/a.jpg", "DATA-10", 1),
                ("https://x/keep.jpg", "DATA-01", 1),
            ]
            assert await conn.fetchval("SELECT policy FROM source WHERE id = 'DATA-10'") == "STORE"
        finally:
            await conn.close()

    asyncio.run(main())

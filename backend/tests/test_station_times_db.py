"""역간 소요시간 조회 DB 테스트 (COMP-12, FR-413). 코스 계산(TravelTimeEstimator)이 쓰는 표.

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다(station·station_time을 비운다).
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_station_times_db.py
"""

import asyncio
import os

import asyncpg
import pytest

from app.data import station_times

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")


def test_load_and_between() -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute("TRUNCATE station_time, station RESTART IDENTITY CASCADE")
            await conn.executemany(
                "INSERT INTO station (id, name, line, lat, lon)"
                " VALUES ($1, $2, '1호선', 35.1, 129)",
                [("110", "남포"), ("111", "자갈치")],
            )
            await conn.execute(
                "INSERT INTO station_time VALUES ('110', '111', 1.8), ('111', '110', 2.1)"
            )
            t = await station_times.load(conn)
            assert len(t) == 2
            assert t.between("110", "111") == 1.8 and t.between("111", "110") == 2.1
            assert t.between("110", "110") == 0.0
            assert t.between("110", "999") is None and t.between(None, "110") is None
            assert await station_times.between(conn, "111", "110") == 2.1
            assert await station_times.between(conn, "110", "999") is None
        finally:
            await conn.close()

    asyncio.run(main())

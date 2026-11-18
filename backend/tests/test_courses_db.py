"""코스 경로 DB 테스트: 역간 소요시간 반영·조회와 경로 계산 (BR-12, FR-413, #76).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고
시험 데이터를 넣은 뒤 끝나면 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_courses_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.data import stations as station_repo
from app.services.course_service import CourseService, StartSpec, reset_network_cache

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

M = 1 / 111_195
FIXTURE = f"""
INSERT INTO station (id, name, line, lat, lon) VALUES
 ('119', '서면', '1호선', 35.157, 129.059), ('219', '서면', '2호선', 35.1575, 129.0595),
 ('120', '범내골', '1호선', 35.147, 129.059), ('220', '전포', '2호선', 35.155, 129.069);
INSERT INTO location (id, name, name_norm, gu, lat, lon) VALUES
 (1, '범내골 촬영지', '범내골촬영지', '부산진구', {35.147 + 30 * M}, 129.059),
 (2, '전포 촬영지', '전포촬영지', '부산진구', {35.155 + 30 * M}, 129.069),
 (3, '좌표 없음', '좌표없음', '부산진구', NULL, NULL);
"""


# 역간 소요시간(분): 배치(#84)가 계산해 둔 값이라고 보고 직접 넣는다
TIMES_SQL = """
INSERT INTO station_time (from_id, to_id, minutes) VALUES
 ('119', '219', 2), ('219', '119', 2), ('119', '120', 2), ('120', '119', 2),
 ('219', '220', 2), ('220', '219', 2), ('120', '220', 6), ('220', '120', 6),
 ('120', '219', 4), ('219', '120', 4), ('119', '220', 4), ('220', '119', 4);
"""


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
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
            await body(conn)
        finally:
            reset_network_cache()  # 시험 데이터로 읽은 역 정보를 다른 테스트에 남기지 않는다
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


def test_load_network_reads_stations_and_times() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await conn.execute(TIMES_SQL)
        net = await station_repo.load_network(conn)

        assert len(net.stations) == 4 and len(net.times) == 12
        assert net.times.between("120", "220") == 6.0
        assert (await station_repo.get_station(conn, "219")).line == "2호선"  # type: ignore[union-attr]
        assert await station_repo.get_station(conn, "999") is None

    run(body)


def test_plan_custom_with_db_network() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await conn.execute(TIMES_SQL)
        service = CourseService(connect=lambda: _borrow(conn))
        start = StartSpec("station", id="120")

        plan = await service.plan_custom(start, [2, 1])

        assert [s["id"] for s in plan["stops"]] == [1, 2]
        beomnae_to_jeonpo = plan["legs"][1]
        assert beomnae_to_jeonpo["mode"] == "subway"  # 직선 약 1.2km·도보 약 23분 > 지하철 약 10분
        assert (beomnae_to_jeonpo["board"]["id"], beomnae_to_jeonpo["alight"]["id"]) == (
            "120",
            "220",
        )
        assert plan["subway_data"] is True

    run(body)

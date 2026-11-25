"""추천 코스 후보 생성·수정·승인 DB 테스트 (API-024, FR-614). 테스트 전용 DB가 있을 때만 돈다.
한 트랜잭션 안에서 표를 비우고 시험 데이터를 넣은 뒤 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_course_admin_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.services.course_admin import CourseAdminError, CourseAdminService, StopEdit
from app.services.course_service import CourseService, reset_network_cache

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 테스트 전용 가짜 데이터. 해운대구에 동서로 늘어선 4곳(1 → 2 → 3 → 4), 멀리 떨어진 5, 좌표 없는 6
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '시험', '영화 속 부산정보', 'https://example.org/1', '시험', 'STORE');
INSERT INTO station (id, name, line, lat, lon) VALUES ('203', '해운대', '2호선', 35.1634, 129.1586);
INSERT INTO location (id, name, name_norm, gu, lat, lon, nearest_station_id, station_walk_min)
VALUES
 (1, '서쪽', '서쪽', '해운대구', 35.16, 129.150, '203', 12),
 (2, '가운데', '가운데', '해운대구', 35.16, 129.153, '203', 8),
 (3, '동쪽', '동쪽', '해운대구', 35.16, 129.156, '203', 5),
 (4, '더 동쪽', '더동쪽', '해운대구', 35.16, 129.159, '203', 3),
 (5, '먼 곳', '먼곳', '해운대구', 35.20, 129.200, NULL, NULL),
 (6, '좌표 없음', '좌표없음', '해운대구', NULL, NULL, NULL, NULL),
 (7, '영도 한 곳', '영도한곳', '영도구', 35.09, 129.04, NULL, NULL);
INSERT INTO location (id, name, name_norm, gu, lat, lon, caution, cert_excluded) VALUES
 (8, '주거지', '주거지', '해운대구', 35.16, 129.1531, 'residential', FALSE),
 (9, '인증 제외', '인증제외', '해운대구', 35.16, 129.1532, NULL, TRUE);
INSERT INTO work (id, title, title_norm, release_year, source_id) VALUES
 (1, '해운대', '해운대', 2009, 'DATA-01'), (2, '친구', '친구', 2001, 'DATA-01');
INSERT INTO shooting (work_id, location_id, source_id) VALUES
 (1, 1, 'DATA-01'), (1, 2, 'DATA-01'), (1, 3, 'DATA-01'), (1, 4, 'DATA-01'), (1, 5, 'DATA-01'),
 (1, 6, 'DATA-01'), (1, 8, 'DATA-01'), (1, 9, 'DATA-01'), (2, 3, 'DATA-01'), (2, 7, 'DATA-01');
"""


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[CourseAdminService, CourseService], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        reset_network_cache()
        try:
            await conn.execute(
                "TRUNCATE review_item, course_stop, course, photo, shooting, work_person, person,"
                " work, location, station_time, station, source RESTART IDENTITY CASCADE"
            )
            await conn.execute(FIXTURE)
            courses = CourseService(lambda: _borrow(conn))
            await body(CourseAdminService(lambda: _borrow(conn), courses), courses)
        finally:
            reset_network_cache()
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


def test_candidate_collects_nearby_places_in_short_order() -> None:
    async def body(admin: CourseAdminService, courses: CourseService) -> None:
        c = await admin.create_candidate("work", "1", max_stops=4)

        assert c["status"] == "draft" and c["title"] == "「해운대」 촬영지 코스"
        ids = [s["id"] for s in c["stops"]]
        assert sorted(ids) == [1, 2, 3, 4]  # 먼 곳(5)·좌표 없음(6)·주거지(8)·인증 제외(9) 빠짐
        assert ids in ([1, 2, 3, 4], [4, 3, 2, 1])  # 끝에서 끝으로 한 번에
        assert all(s["stay_min"] == 20 for s in c["stops"]) and c["total_min"] > 80
        assert (await courses.list_recommended())["items"] == []  # 승인 전에는 안 보인다

        d = await admin.create_candidate("district", "해운대구", max_stops=2)
        assert d["title"] == "해운대구 촬영지 코스" and d["stops"][0]["id"] in (1, 2, 3, 4)
        listed = await admin.list("draft", None)
        assert {i["id"] for i in listed["items"]} == {c["id"], d["id"]}
        assert all(i["problem"] is None and i["total_min"] for i in listed["items"])

    run(body)


def test_candidate_errors() -> None:
    async def body(admin: CourseAdminService, courses: CourseService) -> None:
        for theme, ref, code in [
            ("work", "99", "THEME_NOT_FOUND"),
            ("person", "1", "THEME_NOT_FOUND"),
            ("work", "x1", "INVALID_REQUEST"),
            ("district", "서울", "INVALID_REQUEST"),
            ("district", "영도구", "NOT_ENOUGH_PLACES"),  # 좌표 있는 곳이 1곳
        ]:
            with pytest.raises(CourseAdminError) as e:
                await admin.create_candidate(theme, ref)
            assert e.value.code == code

    run(body)


def test_edit_approve_then_users_see_it() -> None:
    async def body(admin: CourseAdminService, courses: CourseService) -> None:
        c = await admin.create_candidate("work", "1", max_stops=3)
        cid = c["id"]
        edited = await admin.update(
            cid,
            title=" 해운대 한 바퀴 ",
            stops=[StopEdit(3, 30), StopEdit(1), StopEdit(5, 0)],
            start_station_id="203",
        )
        assert edited["title"] == "해운대 한 바퀴" and edited["start"]["type"] == "station"
        assert [(s["id"], s["stay_min"]) for s in edited["stops"]] == [(3, 30), (1, 20), (5, 0)]
        assert edited["start_station_id"] == "203"

        approved = await admin.set_status(cid, True)
        assert approved["status"] == "approved" and approved["approved_at"] is not None
        listed = (await courses.list_recommended())["items"]
        assert [(i["id"], i["total_min"]) for i in listed] == [(cid, approved["total_min"])]
        assert (await courses.get_course(cid))["stops"][0]["id"] == 3

        with pytest.raises(CourseAdminError) as e:
            await admin.delete_draft(cid)
        assert e.value.code == "COURSE_APPROVED"
        again = await admin.update(
            cid, title="고치면 다시 승인 전"
        )  # 고치면 사용자 화면에서 빠진다
        assert again["status"] == "draft" and again["approved_at"] is None
        assert (await courses.list_recommended())["items"] == []
        assert (await admin.update(cid, clear_start=True))["start_station_id"] is None
        await admin.delete_draft(cid)
        with pytest.raises(CourseAdminError) as e:
            await admin.get(cid)
        assert e.value.code == "COURSE_NOT_FOUND"

    run(body)


def test_edit_rejects_bad_stops() -> None:
    async def body(admin: CourseAdminService, courses: CourseService) -> None:
        cid = (await admin.create_candidate("work", "1", max_stops=2))["id"]
        for stops, code in [
            ([StopEdit(1)], "INVALID_REQUEST"),
            ([StopEdit(1), StopEdit(1)], "INVALID_REQUEST"),
            ([StopEdit(1), StopEdit(6)], "LOCATION_NOT_LOCATED"),
            ([StopEdit(1), StopEdit(99)], "LOCATION_NOT_FOUND"),
        ]:
            with pytest.raises(CourseAdminError) as e:
                await admin.update(cid, stops=stops)
            assert e.value.code == code
        with pytest.raises(CourseAdminError) as e:
            await admin.update(cid, start_station_id="999")
        assert e.value.code == "STATION_NOT_FOUND"

    run(body)

"""검색 템플릿 T1~T4 DB 테스트 (FR-404, ADR-010·011). 스키마(db/init)를 적재한 '테스트 전용'
DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고 시험 데이터를 넣은 뒤 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_search_templates_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.services.query_service import AnchorFilter, Filters, Ref, SearchPage, TravelFilter
from app.services.template_searcher import TemplateSearcher

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 테스트 전용 가짜 데이터: 작품-촬영지-인물 연결과 좌표·소요시간은 실제 정보가 아니다
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '시험', '영화 속 부산정보', 'https://example.org/1', '시험', 'STORE'),
 ('DATA-02', '시험', '씨네맵', 'https://example.org/2', '시험', 'STORE');
INSERT INTO station (id, name, line, lat, lon, gu) VALUES
 ('119', '서면', '1호선', 35.1579, 129.0594, '부산진구'),
 ('113', '부산', '1호선', 35.1152, 129.0422, '동구'),
 ('203', '해운대', '2호선', 35.1634, 129.1586, '해운대구'),
 ('209', '광안', '2호선', 35.1576, 129.1131, '수영구');
INSERT INTO station_time (from_id, to_id, minutes) VALUES
 ('119','113',12), ('113','119',12), ('119','203',28), ('203','119',28),
 ('119','209',18), ('209','119',18), ('209','203',10), ('203','209',10);
INSERT INTO location (id, name, name_norm, gu, lat, lon, nearest_station_id, station_walk_min)
VALUES
 (1, '해운대해수욕장', '해운대해수욕장', '해운대구', 35.1587, 129.1604, '203', 9),
 (2, '광안리해수욕장', '광안리해수욕장', '수영구', 35.1532, 129.1186, '209', 11),
 (3, '서면 젊음의거리', '서면젊음의거리', '부산진구', 35.1557, 129.0598, '119', 4),
 (4, '영도대교', '영도대교', '영도구', 35.0950, 129.0368, '113', 31),
 (5, '좌표 없는 곳', '좌표없는곳', NULL, NULL, NULL, NULL, NULL);
INSERT INTO work (id, title, title_norm, release_year, genres, kmdb_id, source_id) VALUES
 (1, '범죄도시', '범죄도시', 2017, '{범죄,액션}', 'K1', 'DATA-01'),
 (2, '부산행', '부산행', 2016, '{액션,스릴러}', NULL, 'DATA-01'),
 (3, '해운대', '해운대', 2009, '{드라마,재난}', NULL, 'DATA-02'),
 (4, '친구', '친구', 2001, '{드라마}', NULL, 'DATA-02');
INSERT INTO person (id, name) VALUES (1, '마동석'), (2, '윤제균');
INSERT INTO work_person (work_id, person_id, role, ord) VALUES
 (1, 1, 'actor', 1), (2, 1, 'actor', 2), (3, 2, 'director', 1);
INSERT INTO shooting (work_id, location_id, source_id) VALUES
 (1, 3, 'DATA-01'), (1, 2, 'DATA-01'), (2, 4, 'DATA-01'), (3, 1, 'DATA-02'),
 (3, 2, 'DATA-02'), (3, 2, 'DATA-01'), (4, 4, 'DATA-02'), (4, 5, 'DATA-02');
"""

MA = Ref("1", "마동석")
SEOMYEON = AnchorFilter("station", Ref("119", "서면역"))
GWANGALLI = AnchorFilter("place", Ref("2", "광안리해수욕장"))


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[TemplateSearcher], Awaitable[None]]) -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "TRUNCATE dict_entry, review_item, course_stop, course, photo, shooting,"
                " work_person, person, work, location, station_time, station, source"
                " RESTART IDENTITY CASCADE"
            )
            await conn.execute(FIXTURE)
            await body(TemplateSearcher(lambda: _borrow(conn)))
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


async def find(s: TemplateSearcher, base_point=None, limit=20, **kw: object) -> SearchPage:
    return await s.search(Filters(**kw), base_point=base_point, limit=limit)  # type: ignore[arg-type]


def ids(page: SearchPage) -> list[int]:
    return [h.location_id for h in page.hits]


def test_t1_orders_by_matched_work_count_and_skips_unlocated() -> None:
    async def body(s: TemplateSearcher) -> None:
        page = await find(s)
        assert page.total == 4 and ids(page) == [2, 4, 1, 3]  # 작품 2편인 곳 먼저, 5번은 좌표 없음
        page = await find(s, limit=2)
        assert page.total == 4 and ids(page) == [2, 4]

    run(body)


def test_t1_matched_conditions() -> None:
    async def body(s: TemplateSearcher) -> None:
        assert ids(await find(s, persons=(MA,))) == [2, 3, 4]
        assert ids(await find(s, works=(Ref("3", "해운대"), Ref("4", "친구")))) == [1, 2, 4]
        assert ids(await find(s, genres=("재난",))) == [1, 2]  # 묶음 → KMDb 장르
        assert ids(await find(s, districts=("수영구", "영도구"))) == [2, 4]
        assert ids(await find(s, year_from=2010, year_to=2019)) == [2, 3, 4]
        assert ids(await find(s, year_to=2005)) == [4]
        # 조건은 한 작품이 모두 만족해야 한다: 마동석 + 2017년 → 범죄도시
        assert ids(await find(s, persons=(MA,), year_from=2017)) == [2, 3]
        page = await find(s, persons=(MA,), genres=("공포",))
        assert (page.total, page.hits) == (0, ())

    run(body)


def test_hit_has_works_station_and_sources() -> None:
    async def body(s: TemplateSearcher) -> None:
        hit = next(h for h in (await find(s, persons=(MA,))).hits if h.location_id == 2)
        assert (hit.name, hit.gu, hit.lat, hit.lon) == (
            "광안리해수욕장",
            "수영구",
            35.1532,
            129.1186,
        )
        # 조건에 맞는 작품(범죄도시) 먼저, 같은 작품이 출처 두 곳에 있어도 한 번만
        assert [(w.title, w.year) for w in hit.works] == [("범죄도시", 2017), ("해운대", 2009)]
        assert (hit.station, hit.walk_min) == ("광안역", 11)
        # 조건에 맞는 촬영 기록 출처 + 좌표(DATA-06) + 역(DATA-08) + KMDb 연결 작품(DATA-04)
        assert hit.source_ids == ("DATA-01", "DATA-04", "DATA-06", "DATA-08")
        hit = next(h for h in (await find(s)).hits if h.location_id == 4)
        assert hit.source_ids == ("DATA-01", "DATA-02", "DATA-06", "DATA-08")

    run(body)


def test_t2_near_point_from_station_place_and_current_position() -> None:
    async def body(s: TemplateSearcher) -> None:
        walk10 = TravelFilter("walk", 10)  # 반경 약 513m
        assert ids(await find(s, anchor=SEOMYEON, travel=walk10)) == [3]
        assert ids(await find(s, anchor=GWANGALLI, travel=walk10)) == [2]
        # 광안리에서 해운대해수욕장까지 약 3.8km: 걸어서 60분(약 3.1km) 밖, 80분(약 4.1km) 안
        assert ids(await find(s, anchor=GWANGALLI, travel=TravelFilter("walk", 60))) == [2]
        assert ids(await find(s, anchor=GWANGALLI, travel=TravelFilter("walk", 80))) == [2, 1]
        assert ids(await find(s, anchor=GWANGALLI)) == [2]  # 분을 말하지 않으면 1km
        here = (35.1600, 129.1600)
        page = await find(s, base_point=here, anchor=AnchorFilter("current"), travel=walk10)
        assert ids(page) == [1]
        page = await find(s, anchor=SEOMYEON, travel=walk10, persons=(Ref("2", "윤제균"),))
        assert page.total == 0
        gone = AnchorFilter("place", Ref("5", "좌표 없는 곳"))  # 기준점 좌표가 없으면 결과 없음
        assert (await find(s, anchor=gone, travel=walk10)).total == 0

    run(body)


def test_t3_any_station_walk() -> None:
    async def body(s: TemplateSearcher) -> None:
        any_station = AnchorFilter("station")
        walk10 = TravelFilter("walk", 10)
        assert ids(await find(s, anchor=any_station, travel=walk10)) == [3, 1]  # 4분, 9분
        assert ids(await find(s, anchor=any_station, travel=TravelFilter("walk", 11))) == [3, 1, 2]
        # 시연 질의: 마동석 나온 작품 촬영지 중 지하철역에서 걸어서 10분 안
        assert ids(await find(s, anchor=any_station, travel=walk10, persons=(MA,))) == [3]

    run(body)


def test_t3_subway_from_named_station() -> None:
    async def body(s: TemplateSearcher) -> None:
        async def within(minutes: int) -> list[int]:
            travel = TravelFilter("subway", minutes)
            return ids(await find(s, anchor=SEOMYEON, travel=travel))

        assert await within(20) == [3]  # 같은 역: 열차 0분 + 도보 4분
        assert await within(30) == [3, 2]  # 서면→광안 18분 + 도보 11분 = 29분
        assert await within(45) == [3, 2, 1, 4]  # 해운대 28+9=37분, 부산 12+31=43분
        # 소요시간 표에 없는 구간(부산→해운대)은 갈 수 없는 것으로 본다
        busan = AnchorFilter("station", Ref("113", "부산역"))
        assert ids(await find(s, anchor=busan, travel=TravelFilter("subway", 300))) == [3, 4]

    run(body)


def test_t3_subway_from_place_starts_at_its_nearest_station() -> None:
    async def body(s: TemplateSearcher) -> None:
        # 광안리해수욕장 → 광안역 직선 약 700m = 걸어서 약 13.7분. 남은 시간으로 T3
        async def within(minutes: int) -> list[int]:
            travel = TravelFilter("subway", minutes)
            return ids(await find(s, anchor=GWANGALLI, travel=travel))

        assert await within(10) == []  # 역까지 걷는 데 다 쓴다
        assert await within(30) == [2]  # 남은 16분: 광안역 도보 11분
        assert await within(40) == [2, 1, 3]  # 남은 26분: 해운대 10+9, 서면 18+4

    run(body)


def test_t4_works_by_location_count() -> None:
    async def body(s: TemplateSearcher) -> None:
        page = await s.search_works(Filters(result_type="work"), limit=20)
        assert page.total == 4
        assert [(w.title, w.location_count) for w in page.rows] == [
            ("범죄도시", 2),
            ("해운대", 2),
            ("부산행", 1),
            ("친구", 1),  # 좌표 없는 촬영지는 세지 않는다
        ]
        assert page.rows[1].source_ids == ("DATA-01", "DATA-02")
        page = await s.search_works(Filters(districts=("수영구",), persons=(MA,)), limit=1)
        assert (page.total, [w.title for w in page.rows]) == (1, ["범죄도시"])

    run(body)

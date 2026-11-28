"""MapService·LocationRepository DB 테스트 (FR-201~210, ADR-010·011, #33).

스키마(db/init)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고
시험 데이터를 넣은 뒤 끝나면 되돌리므로 기존 데이터는 바뀌지 않는다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_map_service_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.services import map_service
from app.services.map_service import MapService

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

BASE = (35.1, 129.0)
M_PER_DEG_LAT = 111_195  # 위도 1도 ≈ 111.2km

FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '부산영상위원회', '영화 속 부산정보', 'https://example.org/1', '제한 없음', 'STORE'),
 ('DATA-02', '부산영상위원회', '씨네맵', 'https://example.org/2', '제한 없음', 'STORE'),
 ('DATA-04', '한국영상자료원', 'KMDb', 'https://example.org/4', '시험', 'STORE'),
 ('DATA-05', '한국관광공사', 'TourAPI', 'https://example.org/5', '공공누리 1유형', 'STORE');
INSERT INTO station (id, name, line, lat, lon, gu) VALUES
 ('111', '시험역', '1호선', 35.1, 129.0, '중구');
INSERT INTO work (id, title, title_norm, release_year, genres, kmdb_id, match_status, source_id)
VALUES
 (1, '범죄영화', '범죄영화', 2018, '{범죄}', 'K1', 'matched', 'DATA-01'),
 (2, '옛날드라마', '옛날드라마', 2001, '{}', NULL, 'unmatched', 'DATA-01'),
 (3, '웃긴영화', '웃긴영화', 2010, '{코메디}', 'K3', 'matched', 'DATA-01'),
 (4, '새영화', '새영화', NULL, '{드라마}', 'K4', 'matched', 'DATA-01');
-- 기준점 (35.1, 129.0)에서 북쪽으로 떨어진 거리: 0.004도 ≈ 445m
INSERT INTO location (id, name, name_norm, address, gu, lat, lon, tags, caution, safety_note,
                      cert_radius_m, cert_excluded, nearest_station_id, station_walk_min) VALUES
 (1, '가까운곳', '가까운곳', '부산 중구 1', '중구', 35.104, 129.0, '{바다}', NULL, NULL,
  100, FALSE, '111', 5.04),
 (2, '중간', '중간', NULL, '서구', 35.108, 129.0, '{}', NULL, NULL, 100, FALSE, NULL, NULL),
 (3, '먼곳', '먼곳', NULL, '중구', 35.12, 129.0, '{}', 'school', '차 조심', 150, FALSE, NULL, NULL),
 (4, '좌표없음', '좌표없음', NULL, '사상구', NULL, NULL, '{}', 'residential', NULL,
  100, FALSE, NULL, NULL),
 (5, '반경밖', '반경밖', NULL, '중구', 35.14, 129.0, '{}', NULL, NULL, 100, FALSE, NULL, NULL),
 -- 북·동으로 445m씩: earth_box(500m) 안이지만 직선거리 약 630m
 (6, '모서리', '모서리', NULL, '중구', 35.104, 129.0049, '{}', NULL, NULL, 100, TRUE, NULL, NULL);
INSERT INTO shooting (work_id, location_id, source_id) VALUES
 (1, 1, 'DATA-01'), (1, 1, 'DATA-02'), (2, 1, 'DATA-01'), (4, 1, 'DATA-01'),
 (3, 2, 'DATA-02'), (1, 3, 'DATA-01'), (2, 4, 'DATA-01'), (3, 5, 'DATA-01'), (2, 6, 'DATA-01');
INSERT INTO photo (location_id, url, kogl_type, title, source_id) VALUES
 (1, 'https://example.org/p.jpg', 1, '전경', 'DATA-05');
"""


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[MapService], Awaitable[None]]) -> None:
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
            await body(MapService(connect=lambda: _borrow(conn)))
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


async def search(service: MapService, radius_m: int = 1000, **kw: object) -> dict:
    return await service.search(lat=BASE[0], lon=BASE[1], radius_m=radius_m, **kw)  # type: ignore[arg-type]


def names(result: dict) -> list[str]:
    return [h["name"] for h in result["items"]]


def test_radius_uses_circle_not_box_and_sorts_by_distance() -> None:
    async def body(s: MapService) -> None:
        r500, r1000, r3000 = [await search(s, r) for r in (500, 1000, 3000)]

        assert names(r500) == ["가까운곳"]  # 모서리(630m)는 상자 안이지만 원 밖
        assert names(r1000) == ["가까운곳", "모서리", "중간"]
        assert names(r3000) == [
            "가까운곳",
            "모서리",
            "중간",
            "먼곳",
        ]  # 좌표없음·반경밖 제외(FR-210)
        assert r3000["total"] == 4
        d = [h["distance_m"] for h in r3000["items"]]
        assert d == sorted(d) and abs(d[0] - 0.004 * M_PER_DEG_LAT) < 5

    run(body)


def test_hit_card_fields() -> None:
    async def body(s: MapService) -> None:
        near = (await search(s, 500))["items"][0]

        assert near["work_count"] == 3  # 출처 두 곳에 있는 작품은 한 번만
        assert [w["title"] for w in near["works"]] == ["범죄영화", "옛날드라마", "새영화"]
        assert near["nearest_station"] == {
            "id": "111",
            "name": "시험역",
            "line": "1호선",
            "walk_min": 5.0,
        }
        assert near["directions_url"].startswith("https://map.kakao.com/link/to/")

    run(body)


def test_work_filters_must_hold_for_one_work() -> None:
    async def body(s: MapService) -> None:
        crime = await search(s, 3000, genres=["범죄·느와르"])
        crime_old = await search(s, 3000, genres=["범죄·느와르"], year_to=2005)
        drama = await search(s, 3000, genres=["드라마"])
        recent = await search(s, 3000, year_from=2009)
        gu = await search(s, 3000, gu=["서구", "사상구"])

        assert names(crime) == ["가까운곳", "먼곳"]
        assert crime["items"][0]["works"][0]["title"] == "범죄영화"  # 조건에 맞는 작품 먼저
        assert names(crime_old) == []  # 범죄(2018)와 2005년 이전을 한 작품이 함께 만족해야 함
        assert names(drama) == ["가까운곳"]  # KMDb 미연결 작품(장르 없음)은 장르 조건에 안 걸림
        assert names(recent) == ["가까운곳", "중간", "먼곳"]  # 개봉연도 없는 작품은 연도 조건 제외
        assert names(gu) == ["중간"]

    run(body)


def test_limit_keeps_total(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(map_service, "SEARCH_LIMIT", 2)

    async def body(s: MapService) -> None:
        result = await search(s, 3000)

        assert result["total"] == 4 and names(result) == ["가까운곳", "모서리"]

    run(body)


def test_search_sources() -> None:
    async def body(s: MapService) -> None:
        near = await search(s, 500)
        corner = await search(s, 1000, gu=["중구"], year_to=2005)

        assert [c["id"] for c in near["sources"]] == ["DATA-01", "DATA-02", "DATA-04"]
        assert names(corner) == ["가까운곳", "모서리"]  # 2005년 이전 작품(옛날드라마)이 있는 곳
        empty = await search(s, 500, gu=["기장군"])
        assert empty == {"radius_m": 500, "total": 0, "items": [], "sources": []}

    run(body)


def test_location_detail() -> None:
    async def body(s: MapService) -> None:
        near = await s.location(1)
        school = await s.location(3)
        unlocated = await s.location(4)
        excluded = await s.location(6)

        assert near is not None and near["located"] and near["tags"] == ["바다"]
        assert near["photos"] == [
            {"url": "https://example.org/p.jpg", "title": "전경", "source_id": "DATA-05"}
        ]
        assert [(w["title"], w["kmdb_matched"]) for w in near["works"]] == [
            ("범죄영화", True),
            ("옛날드라마", False),
            ("새영화", True),
        ]
        assert [c["id"] for c in near["sources"]] == ["DATA-01", "DATA-02", "DATA-04", "DATA-05"]
        assert near["cert"] == {"eligible": True, "radius_m": 100, "excluded_reason": None}

        assert (
            school is not None and school["caution_notice"] and school["safety_note"] == "차 조심"
        )
        assert school["cert"] == {"eligible": False, "radius_m": 150, "excluded_reason": "school"}

        assert unlocated is not None and not unlocated["located"]
        assert unlocated["directions_url"] is None and unlocated["lat"] is None
        assert unlocated["cert"]["excluded_reason"] == "no_coordinates"
        assert [c["id"] for c in unlocated["sources"]] == ["DATA-01"]  # KMDb 연결 작품 없음

        assert excluded is not None and excluded["cert"]["excluded_reason"] == "excluded"
        assert await s.location(999) is None

    run(body)


def test_meta_reads_stations_and_years() -> None:
    async def body(s: MapService) -> None:
        meta = await s.meta()

        assert meta["stations"] == [
            {"id": "111", "name": "시험역", "line": "1호선", "lat": 35.1, "lon": 129.0}
        ]
        assert meta["year_range"] == {"min": 2001, "max": 2018}
        assert len(meta["districts"]) == 16 and len(meta["genres"]) == 12

    run(body)


def test_persons_finds_by_name_and_tells_namesakes_apart() -> None:  # API-026, FR-310
    from app.data import dictionary

    async def body(s: MapService) -> None:
        async with s._connect() as conn:  # noqa: SLF001
            await conn.execute(
                "INSERT INTO person (id, name) VALUES (1, '송강호'), (2, '송강호'), (3, '마동석');"
                "INSERT INTO work_person (work_id, person_id, role, ord) VALUES"
                " (1, 1, 'actor', 1), (3, 2, 'actor', 1), (3, 3, 'actor', 2)"
            )
            await dictionary.rebuild(conn)

        same = await s.persons("송강호")
        assert sorted((p["id"], p["work"]) for p in same) == [(1, "범죄영화"), (2, "웃긴영화")]
        assert [p["name"] for p in await s.persons("마동")] == ["마동석"]  # 일부만 써도 찾음
        assert await s.persons("없는사람") == []

    run(body)

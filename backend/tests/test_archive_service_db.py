"""ArchiveService·WorkRepository DB 테스트 (FR-101~107·802, API-001~003).

스키마(db/init 01~04)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고
시험 데이터를 넣은 뒤 끝나면 되돌리므로 기존 데이터는 바뀌지 않는다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_archive_service_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.services.archive_service import ArchiveService

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 작품·인물·장소는 모두 지어낸 것이다
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '부산영상위원회', '영화 속 부산정보', 'https://example.org/1', '제한 없음', 'STORE'),
 ('DATA-02', '부산영상위원회', '씨네맵', 'https://example.org/2', '제한 없음', 'STORE'),
 ('DATA-04', '한국영상자료원', 'KMDb', 'https://example.org/4', '시험', 'STORE'),
 ('DATA-05', '영화진흥위원회', 'KOBIS', 'https://example.org/5', '저장 금지', 'ID_ONLY');
INSERT INTO work (id, title, title_norm, release_year, genres, kmdb_id, kmdb, match_status,
                  source_id, kobis_movie_cd) VALUES
 (1, '부산 항구', '부산항구', 2018, '{범죄,액션}', 'K1',
  '{"plot": "가짜 줄거리", "keywords": ["항구"], "link": "https://example.org/k1",
    "source_id": "DATA-04"}',
  'matched', 'DATA-01', '20990001'),
 (2, '옛날 드라마', '옛날드라마', 2001, '{}', NULL, NULL, 'unmatched', 'DATA-02', NULL),
 (3, '웃긴 영화', '웃긴영화', 2010, '{코메디}', 'K3', NULL, 'matched', 'DATA-01', NULL),
 (4, '가나다', '가나다', NULL, '{범죄}', 'K4', NULL, 'matched', 'DATA-01', NULL);
INSERT INTO person (id, name) VALUES (1, '김감독'), (2, '이 배우'), (3, '박배우'), (4, '최배우'),
 (5, '정배우'), (6, '강배우'), (7, '조배우');
INSERT INTO work_person (work_id, person_id, role, ord) VALUES
 (1, 1, 'director', 0), (1, 2, 'actor', 0), (1, 3, 'actor', 1), (1, 4, 'actor', 2),
 (1, 5, 'actor', 3), (1, 6, 'actor', 4), (1, 7, 'actor', 5), (3, 2, 'actor', 0);
INSERT INTO location (id, name, name_norm, gu, lat, lon) VALUES
 (1, '부두', '부두', '중구', 35.1, 129.0), (2, '골목', '골목', '부산시', NULL, NULL),
 (3, '시장', '시장', '서구', 35.11, 129.01);
INSERT INTO shooting (work_id, location_id, source_id) VALUES
 (1, 1, 'DATA-01'), (1, 1, 'DATA-02'), (1, 2, 'DATA-01'), (1, 3, 'DATA-01'),
 (2, 1, 'DATA-02'), (2, 3, 'DATA-02'), (3, 3, 'DATA-01'), (4, 2, 'DATA-01');
"""


class Ratings:
    async def rating(self, movie_cd: str, timeout_s: float) -> str | None:
        return f"등급({movie_cd})"


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


def run(body: Callable[[ArchiveService], Awaitable[None]]) -> None:
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
            await body(ArchiveService(connect=lambda: _borrow(conn), ratings=Ratings()))
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


def ids(result: dict) -> list[int]:
    return [w["id"] for w in result["items"]]


def test_list_sorts_and_pages() -> None:
    async def body(svc: ArchiveService) -> None:
        by_title = await svc.list_works()
        assert ids(by_title) == [4, 1, 2, 3] and by_title["total"] == 4  # 이름순(기본)
        assert ids(await svc.list_works(sort="year")) == [1, 3, 2, 4]  # 최신순, 연도 없으면 끝
        assert ids(await svc.list_works(sort="locations")) == [1, 2, 3, 4]  # 촬영지 많은 순
        first = by_title["items"][1]
        assert first["location_count"] == 3  # 출처 두 곳의 같은 촬영지는 한 번만
        assert first["director"] == "김감독" and first["kmdb_matched"] is True
        assert {s["id"] for s in by_title["sources"]} == {"DATA-01", "DATA-02", "DATA-04"}
        page2 = await svc.list_works(page=2, size=3)
        assert ids(page2) == [3] and page2["total"] == 4 and page2["page"] == 2
        assert (await svc.list_works(page=9))["items"] == []

    run(body)


def test_list_search_and_genre() -> None:
    async def body(svc: ArchiveService) -> None:
        assert ids(await svc.list_works(q="부산  항구")) == [1]  # 공백·문장부호 무시(FR-102)
        assert ids(await svc.list_works(q="이배우")) == [1, 3]  # 배우 이름
        assert ids(await svc.list_works(q="김감독")) == [1]  # 감독 이름
        assert ids(await svc.list_works(q="%")) == []  # 와일드카드로 쓰이지 않는다
        assert ids(await svc.list_works(genre="범죄")) == [4, 1]
        assert ids(await svc.list_works(q="항구", genre="코메디")) == []
        empty = await svc.list_works(q="없는작품")
        assert empty["total"] == 0 and empty["sources"] == []

    run(body)


def test_detail_matched_work() -> None:
    async def body(svc: ArchiveService) -> None:
        d = await svc.get_work(1)
        assert d is not None and d["title"] == "부산 항구" and d["director"] == "김감독"
        assert d["cast"] == ["이 배우", "박배우", "최배우", "정배우", "강배우"]  # 최대 5명(FR-103)
        assert d["synopsis"] == "가짜 줄거리"
        assert d["kmdb_url"] == "https://example.org/k1"
        assert "poster_url" not in d and "still_urls" not in d and d["trailer_url"] is None
        locs = d["locations"]
        assert [x["name"] for x in locs] == ["부두", "시장", "골목"]  # 좌표 있는 곳 먼저
        assert locs[2] == {
            "id": 2,
            "name": "골목",
            "gu": None,
            "located": False,
            "lat": None,
            "lon": None,
        }  # FR-210, 16개 구·군 밖 값은 비움
        # 겹치는 촬영지·인물 수가 많은 순, 같으면 최신작 먼저(FR-802): 3번 2, 2번 2, 4번 1
        assert [w["id"] for w in d["related"]] == [3, 2, 4]
        assert [s["id"] for s in d["sources"]] == ["DATA-01", "DATA-02", "DATA-04"]

    run(body)


def test_detail_unmatched_and_missing() -> None:
    async def body(svc: ArchiveService) -> None:
        d = await svc.get_work(2)
        assert d is not None and d["kmdb_matched"] is False and d["synopsis"] is None
        assert d["cast"] == [] and d["director"] is None and len(d["locations"]) == 2
        assert [s["id"] for s in d["sources"]] == ["DATA-02"]  # KMDb 출처 없음(FR-604)
        assert await svc.get_work(999) is None

    run(body)


def test_rating_reads_code_only() -> None:
    async def body(svc: ArchiveService) -> None:
        ok = await svc.get_rating(1)
        assert ok is not None and ok["rating"] == "등급(20990001)"
        assert [s["id"] for s in ok["sources"]] == ["DATA-05"]
        assert await svc.get_rating(2) == {"rating": None, "sources": []}  # 코드 없음
        assert await svc.get_rating(999) is None

    run(body)

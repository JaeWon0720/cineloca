"""장르·감독·출연 반영 DB 테스트 (FR-103·205·310, app/data/credits.py). 스키마(db/init)를 적재한
'테스트 전용' DB가 있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고 시험 데이터를 넣은 뒤 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_credits_db.py
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable

import asyncpg
import pytest

from app.data import credits, dictionary

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 테스트 전용 가짜 데이터(실제 작품·출연 정보가 아니다)
K1 = {"genres": ["드라마", "가족"], "directors": ["감독가"], "actors": ["배우가", "배우나"]}
K2 = {"genres": ["범죄"], "directors": ["감독가"], "actors": ["배우나", "배우다"]}
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '시험', '시험', 'https://example.org/1', '시험', 'STORE');
INSERT INTO work (id, title, title_norm, release_year, genres, kmdb_id, kmdb, source_id) VALUES
 (1, '가짜일', '가짜일', 2020, '{}', 'K00001', $1::jsonb, 'DATA-01'),
 (2, '가짜이', '가짜이', 2021, '{}', 'F00002', $2::jsonb, 'DATA-01'),
 (3, '연결끊김', '연결끊김', 2022, '{스릴러}', NULL, $2::jsonb, 'DATA-01'),
 (4, '본문없음', '본문없음', 2023, '{스릴러}', 'K00004', NULL, 'DATA-01');
INSERT INTO person (id, name) VALUES (7, '배우나'), (8, '옛사람');
INSERT INTO work_person (work_id, person_id, role, ord) VALUES (4, 8, 'actor', 0);
"""


def run(body: Callable[[asyncpg.Connection], Awaitable[None]]) -> None:
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
            await conn.execute("SELECT setval('person_id_seq', 100)")
            for stmt in FIXTURE.strip().split(";\n"):
                if "$1" in stmt or "$2" in stmt:
                    await conn.execute(stmt, json.dumps(K1), json.dumps(K2))
                elif stmt.strip():
                    await conn.execute(stmt)
            await body(conn)
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


async def credits_of(conn: asyncpg.Connection, work_id: int) -> list[tuple[str, str, int]]:
    rows = await conn.fetch(
        "SELECT wp.role, p.name, wp.ord FROM work_person wp JOIN person p ON p.id = wp.person_id"
        " WHERE wp.work_id = $1 ORDER BY wp.role, wp.ord",
        work_id,
    )
    return [(r["role"], r["name"], r["ord"]) for r in rows]


def test_apply_moves_genres_and_people_from_stored_kmdb() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        counts = await credits.apply(conn)
        genres = dict(await conn.fetch("SELECT id, genres FROM work ORDER BY id"))
        assert genres == {1: ["드라마", "가족"], 2: ["범죄"], 3: [], 4: []}  # 연결·본문 없으면 비움
        assert await credits_of(conn, 1) == [
            ("actor", "배우가", 0),
            ("actor", "배우나", 1),
            ("director", "감독가", 0),
        ]
        assert await credits_of(conn, 3) == [] and await credits_of(conn, 4) == []
        # 이름이 같으면 같은 사람(있던 id 유지), 링크가 없어진 사람도 지우지 않는다
        ids = dict(await conn.fetch("SELECT name, id FROM person"))
        assert ids["배우나"] == 7 and "옛사람" in ids
        assert await conn.fetchval("SELECT count(*) FROM person WHERE name = '감독가'") == 1
        assert counts == {"works": 2, "people": 4, "links": 6}

    run(body)


def test_apply_twice_keeps_person_ids_and_feeds_dictionary() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        await credits.apply(conn)
        before = sorted(await conn.fetch("SELECT id, name FROM person"))
        await credits.apply(conn)
        assert sorted(await conn.fetch("SELECT id, name FROM person")) == before
        await dictionary.rebuild(conn)
        people = await conn.fetch("SELECT surface FROM dict_entry WHERE kind = 'person'")
        assert {r["surface"] for r in people} >= {"배우가", "배우나", "감독가"}

    run(body)

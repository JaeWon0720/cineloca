"""고유명사 사전 DB 테스트 (FR-303, dict_entry). 스키마(db/init)를 적재한 '테스트 전용' DB가
있을 때만 돈다. 한 트랜잭션 안에서 표를 비우고 시험 데이터를 넣은 뒤 되돌린다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_dictionary_db.py
"""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import asyncpg
import pytest

from app.data import dictionary
from app.data.ai_gateway.tasks import QueryConditions
from app.services.entity_resolver import DictionaryResolver, PgDictionary
from app.services.query_service import AnchorFilter, Ref

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

# 테스트 전용 가짜 데이터(실제 작품·출연 정보가 아니다)
FIXTURE = """
INSERT INTO source (id, org, name, url, license, policy) VALUES
 ('DATA-01', '시험', '시험', 'https://example.org/1', '시험', 'STORE');
INSERT INTO station (id, name, line, lat, lon, gu) VALUES
 ('113', '부산', '1호선', 35.115, 129.041, '동구'),
 ('119', '서면', '1호선', 35.158, 129.059, '부산진구'),
 ('219', '서면', '2호선', 35.158, 129.059, '부산진구');
INSERT INTO work (id, title, title_norm, release_year, source_id) VALUES
 (1, '범죄도시', '범죄도시', 2017, 'DATA-01'),
 (2, '친구', '친구', 2001, 'DATA-01'),
 (3, '넷플릭스 친구', '넷플릭스친구', 2020, 'DATA-01'),
 (4, 'JTBC 나의 해방일지', 'jtbc나의해방일지', 2022, 'DATA-01');
INSERT INTO person (id, name) VALUES (1, '마동석'), (2, '김시험'), (3, '김시험'), (4, '출연없음');
INSERT INTO work_person (work_id, person_id, role, ord) VALUES
 (1, 1, 'actor', 1), (1, 2, 'actor', 2), (2, 3, 'director', 1);
INSERT INTO location (id, name, name_norm, gu, lat, lon) VALUES
 (1, '광안리해수욕장', '광안리해수욕장', '수영구', 35.153, 129.118),
 (2, '영도대교', '영도대교', '영도구', 35.095, 129.036),
 (3, '좌표 없는 영도대교 앞', '좌표없는영도대교앞', NULL, NULL, NULL);
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
                "TRUNCATE dict_entry, review_item, course_stop, course, photo, shooting,"
                " work_person, person, work, location, station_time, station, source"
                " RESTART IDENTITY CASCADE"
            )
            await conn.execute(FIXTURE)
            await dictionary.rebuild(conn)
            await body(conn)
        finally:
            await tx.rollback()
            await conn.close()

    asyncio.run(main())


async def ids(conn: asyncpg.Connection, kind: str, text: str, **kw: object) -> list[str]:
    return [m.canonical_id for m in await dictionary.lookup(conn, kind, text, **kw)]  # type: ignore[arg-type]


def test_rebuild_fills_dictionary_from_knowledge_base() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        rows = await conn.fetch("SELECT kind, surface, canonical_id FROM dict_entry")
        got = {(r["kind"], r["surface"], r["canonical_id"]) for r in rows}
        assert {("work", "친구", "2"), ("work", "친구", "3"), ("work", "넷플릭스친구", "3")} <= got
        assert {("work", "jtbc나의해방일지", "4"), ("work", "나의해방일지", "4")} <= got
        assert {
            ("person", "마동석", "1"),
            ("person", "김시험", "2"),
            ("person", "김시험", "3"),
        } <= got
        assert not [g for g in got if g[1] == "출연없음"]  # 작품에 연결되지 않은 인물은 넣지 않는다
        assert {("station", "부산", "113"), ("station", "부산역", "113")} <= got
        assert {("district", "해운대구", "해운대구"), ("district", "해운대", "해운대구")} <= got
        districts = {g[1] for g in got if g[0] == "district"}
        assert len(districts) == 16 + 11 and "중" not in districts
        assert len(got) == len(rows)  # 같은 행을 두 번 넣지 않는다

    run(body)


def test_rebuild_is_idempotent_and_keeps_embeddings_of_unchanged_rows() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        vec = "[" + ",".join(["0.5"] * 1024) + "]"
        await conn.execute(
            "UPDATE dict_entry SET embedding = $1::text::vector WHERE surface = '마동석'", vec
        )
        before = await conn.fetch("SELECT id, kind, surface FROM dict_entry ORDER BY id")
        await conn.execute("DELETE FROM work WHERE id = 4")
        await conn.execute("INSERT INTO station VALUES ('120', '부전', '1호선', 35.16, 129.06)")
        counts = await dictionary.rebuild(conn)
        after = {r["id"] for r in await conn.fetch("SELECT id FROM dict_entry")}
        gone = [r["surface"] for r in before if r["id"] not in after]
        assert sorted(gone) == ["jtbc나의해방일지", "나의해방일지"]
        assert counts["station"] == 6 + 2 and counts["work"] == 4
        assert await conn.fetchval(
            "SELECT embedding IS NOT NULL FROM dict_entry WHERE surface = '마동석'"
        )

    run(body)


def test_lookup_normalizes_query_like_stored_surface() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        (m,) = await dictionary.lookup(conn, "person", "마 동석")
        assert (m.canonical_id, m.score, m.via) == ("1", 1.0, "exact")
        assert await ids(conn, "work", "나의 해방일지") == ["4"]  # 머리말 뗀 제목
        assert await ids(conn, "station", "부산역") == ["113"]
        assert await ids(conn, "district", "해운대") == ["해운대구"]
        assert await ids(conn, "person", "  ") == []

    run(body)


def test_lookup_trigram_threshold() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        # '범죄도시 2' ↔ '범죄도시' 0.57: 통과
        (m,) = await dictionary.lookup(conn, "work", "범죄도시 2")
        assert (m.canonical_id, m.via) == ("1", "trigram") and 0.5 < m.score < 0.6
        # 세 글자 이름의 한 글자 오타는 0.33이라 0.35를 넘지 못한다(임베딩 보완 대상)
        assert await ids(conn, "person", "마동숙") == []
        assert await ids(conn, "work", "마동석") == []  # 다른 종류에서는 찾지 않는다

    run(body)


def test_lookup_same_surface_returns_every_candidate() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        assert await ids(conn, "work", "친구") == ["2", "3"]
        assert await ids(conn, "person", "김시험") == ["2", "3"]
        assert await ids(conn, "station", "서면역") == ["119", "219"]

    run(body)


def test_lookup_embedding_fallback_only_with_query_vector() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        near = [1.0] + [0.0] * 1023
        far = [0.0, 1.0] + [0.0] * 1022
        assert not await dictionary.has_embeddings(conn, "person")
        await conn.execute(
            "UPDATE dict_entry SET embedding = $1::text::vector WHERE surface = '마동석'",
            dictionary._vector_text(near),
        )
        assert await dictionary.has_embeddings(conn, "person")
        assert await ids(conn, "person", "마동숙") == []
        (m,) = await dictionary.lookup(conn, "person", "마동숙", query_vec=near)
        assert (m.canonical_id, m.via) == ("1", "embedding") and m.score > 0.99
        assert await ids(conn, "person", "마동숙", query_vec=far) == []  # 코사인 0.75 미만

    run(body)


def test_lookup_place_finds_located_shooting_places() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        assert [m.canonical_id for m in await dictionary.lookup_place(conn, "영도 대교")] == ["2"]
        # 줄여 부른 이름. 좌표 없는 촬영지는 기준점이 될 수 없어 빠진다
        assert [m.canonical_id for m in await dictionary.lookup_place(conn, "광안리")] == ["1"]
        assert [m.canonical_id for m in await dictionary.lookup_place(conn, "영도대교 앞")] == ["2"]
        assert await dictionary.lookup_place(conn, "태종대") == []

    run(body)


def test_resolver_with_database() -> None:
    async def body(conn: asyncpg.Connection) -> None:
        resolver = DictionaryResolver(PgDictionary(lambda: _borrow(conn)))
        r = await resolver.resolve(
            QueryConditions(
                out_of_scope=False,
                work_titles=["친구", "나의 해방일지"],
                persons=["마 동석", "없는사람"],
                genres=[],
                districts=["수영구"],
                release_year_from=None,
                release_year_to=None,
                anchor={"type": "station", "name": "서면"},
                travel={"mode": "subway", "max_min": 20},
                result_type="location",
            )
        )
        assert r.filters.works == (Ref("4", "JTBC 나의 해방일지"),)
        assert r.filters.persons == (Ref("1", "마동석"),)
        assert r.unknown == ("없는사람",)
        assert r.filters.anchor == AnchorFilter("station", None)
        works, anchor = r.choices
        assert [o.label for o in works.options] == ["친구 (2001)", "넷플릭스 친구 (2020)"]
        assert [o.label for o in anchor.options] == ["서면역 (1호선)", "서면역 (2호선)"]
        assert await resolver.pick(anchor, "station:219") == Ref("219", "서면역")
        assert await resolver.pick(works, "work:2") == Ref("2", "친구")

    run(body)

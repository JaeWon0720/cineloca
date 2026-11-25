"""작품의 장르·감독·출연을 KMDb 저장값(work.kmdb)에서 서비스 표로 옮긴다 (FR-103·205·310).

동기화 반영(staging.publish)이 같은 트랜잭션에서 부른다. 값은 배치가 work.kmdb에 넣은
genres·directors·actors 목록(app/batch/kmdb.py stored_kmdb)이다.
- work.genres: KMDb 장르 이름 그대로. KMDb 연결이 없거나 저장값이 없으면 비운다.
- person·work_person: 매번 work_person을 다시 만든다. 사람은 이름으로 찾고(없으면 만든다)
  지우지 않는다(인물 테마 추천 코스가 person.id를 가리킬 수 있음).
  KMDb CSV에는 인물 번호가 없어 이름이 같으면 같은 사람으로 본다(kmdb_person_id는 비움).
"""

from __future__ import annotations

import asyncpg

_CREDIT = """
    SELECT w.id AS work_id, r.role, x.name, (x.n - 1)::smallint AS ord
    FROM work w
    CROSS JOIN (VALUES ('director', 'directors'), ('actor', 'actors')) AS r(role, key)
    CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE WHEN jsonb_typeof(w.kmdb -> r.key) = 'array' THEN w.kmdb -> r.key
             ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS x(name, n)
    WHERE w.kmdb_id IS NOT NULL AND btrim(x.name) <> ''
"""


async def apply(conn: asyncpg.Connection) -> dict[str, int]:
    """호출하는 쪽이 트랜잭션을 연다. 장르가 있는 작품 수, 연결된 사람 수, 연결 수를 돌려준다."""
    await conn.execute(
        """
        UPDATE work SET genres = CASE
            WHEN kmdb_id IS NOT NULL AND jsonb_typeof(kmdb -> 'genres') = 'array'
            THEN ARRAY(SELECT jsonb_array_elements_text(kmdb -> 'genres'))
            ELSE '{}'::text[] END
        """
    )
    await conn.execute(
        f"""
        INSERT INTO person (name)
        SELECT DISTINCT c.name FROM ({_CREDIT}) c
        WHERE NOT EXISTS (SELECT 1 FROM person p WHERE p.name = c.name)
        """
    )
    await conn.execute("DELETE FROM work_person")
    await conn.execute(
        f"""
        INSERT INTO work_person (work_id, person_id, role, ord)
        SELECT c.work_id, (SELECT min(p.id) FROM person p WHERE p.name = c.name), c.role, c.ord
        FROM ({_CREDIT}) c
        ON CONFLICT (work_id, person_id, role) DO NOTHING
        """
    )
    r = await conn.fetchrow(
        "SELECT (SELECT count(*) FROM work WHERE cardinality(genres) > 0) AS works,"
        " (SELECT count(DISTINCT person_id) FROM work_person) AS people,"
        " (SELECT count(*) FROM work_person) AS links"
    )
    return dict(r)

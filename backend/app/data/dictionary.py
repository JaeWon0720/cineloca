"""고유명사 사전 (COMP-12 dict_entry, FR-403). F4 사전 대조가 쓴다.

- rebuild(): 지식베이스(작품·인물·역)와 구·군 목록에서 dict_entry를 다시 맞춘다.
  동기화 반영(staging.publish)과 같은 트랜잭션에서 부른다. 바뀌지 않은 행은 그대로 두어
  embedding 값이 남는다(임베딩 채우기는 FR-617 작업).
- lookup(): 정확 일치 → 트라이그램 상위 3개(0.35 이상) → 임베딩 코사인 0.75 이상 순으로 찾는다.
  임베딩 보완은 질의 벡터를 받았을 때만 한다.
- 표기(surface)는 저장할 때와 찾을 때 모두 app.core.names.norm으로 정규화한다
  ('마 동석' → '마동석'). work.title_norm·location.name_norm과 같은 규칙이다.
- 값은 모두 바인딩 매개변수로 넘긴다(ADR-010).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import asyncpg

from app.core.names import norm, split_media
from app.core.places import DISTRICT_CENTERS

Kind = Literal["work", "person", "district", "station"]
TargetKind = Literal["work", "person", "station", "location"]

TRIGRAM_MIN = 0.35  # SDD 부록 B 초기값(평가셋으로 확정)
EMBEDDING_MIN = 0.75
TOP_K = 3


@dataclass(frozen=True)
class Match:
    canonical_id: str
    surface: str
    score: float  # 정확 일치 1.0, 트라이그램 유사도, 코사인 유사도
    via: Literal["exact", "trigram", "embedding"]


@dataclass(frozen=True)
class Target:
    """표준 값 하나의 이름과 구분용 정보(고르기 라벨에 쓴다)."""

    id: str
    name: str
    detail: str | None = None  # 작품: 개봉연도, 인물: 대표 작품, 역: 호선, 촬영지: 구·군


def district_surfaces() -> list[tuple[str, str]]:
    """(표기, 구·군 이름). '해운대구'와 '해운대'. '구'를 떼면 한 글자인 곳은 긴 이름만."""
    out: list[tuple[str, str]] = []
    for name in DISTRICT_CENTERS:
        out.append((name, name))
        if len(name) > 2:
            out.append((name[:-1], name))
    return out


def station_surfaces(name: str) -> list[str]:
    """'서면' → ['서면', '서면역'], '부산역' → ['부산역', '부산']."""
    bare = name[:-1] if name.endswith("역") and len(name) > 1 else name
    return [bare, bare + "역"]


async def _entries(conn: asyncpg.Connection) -> set[tuple[str, str, str]]:
    out: set[tuple[str, str, str]] = set()

    def add(kind: Kind, surface: str, canonical_id: object) -> None:
        if s := norm(surface):
            out.add((kind, s, str(canonical_id)))

    for r in await conn.fetch("SELECT id, title FROM work"):
        add("work", r["title"], r["id"])
        add("work", split_media(r["title"])[0], r["id"])  # 'JTBC 나의 해방일지' → '나의 해방일지'
    for r in await conn.fetch(
        "SELECT p.id, p.name FROM person p"
        " WHERE EXISTS (SELECT 1 FROM work_person wp WHERE wp.person_id = p.id)"
    ):
        add("person", r["name"], r["id"])
    for r in await conn.fetch("SELECT id, name FROM station"):
        for surface in station_surfaces(r["name"]):
            add("station", surface, r["id"])
    for surface, name in district_surfaces():
        add("district", surface, name)
    return out


async def rebuild(conn: asyncpg.Connection) -> dict[str, int]:
    """dict_entry를 지식베이스에 맞춘다. 호출하는 쪽이 트랜잭션을 연다. 종류별 행 수를 돌려준다."""
    entries = sorted(await _entries(conn))
    kinds = [e[0] for e in entries]
    surfaces = [e[1] for e in entries]
    ids = [e[2] for e in entries]
    await conn.execute(
        """
        DELETE FROM dict_entry d
        WHERE NOT EXISTS (
            SELECT 1 FROM unnest($1::text[], $2::text[], $3::text[]) AS n(kind, surface, cid)
            WHERE n.kind = d.kind AND n.surface = d.surface AND n.cid = d.canonical_id)
        """,
        kinds,
        surfaces,
        ids,
    )
    await conn.execute(
        """
        INSERT INTO dict_entry (kind, surface, canonical_id)
        SELECT n.kind, n.surface, n.cid
        FROM unnest($1::text[], $2::text[], $3::text[]) AS n(kind, surface, cid)
        WHERE NOT EXISTS (
            SELECT 1 FROM dict_entry d
            WHERE d.kind = n.kind AND d.surface = n.surface AND d.canonical_id = n.cid)
        """,
        kinds,
        surfaces,
        ids,
    )
    rows = await conn.fetch("SELECT kind, count(*) AS n FROM dict_entry GROUP BY kind")
    return {r["kind"]: r["n"] for r in rows}


_EXACT = """
SELECT DISTINCT canonical_id, surface FROM dict_entry
WHERE kind = $1 AND surface = $2
ORDER BY canonical_id
"""

# 한 표준 값에 표기가 여럿이면(머리말 뗀 제목, 'X역') 가장 비슷한 표기 하나만 남긴다
_TRIGRAM = """
SELECT canonical_id, surface, score FROM (
    SELECT DISTINCT ON (canonical_id) canonical_id, surface, similarity(surface, $2) AS score
    FROM dict_entry
    WHERE kind = $1 AND similarity(surface, $2) >= $3
    ORDER BY canonical_id, similarity(surface, $2) DESC, surface
) t
ORDER BY score DESC, canonical_id
LIMIT $4
"""

_EMBEDDING = """
SELECT canonical_id, surface, score FROM (
    SELECT DISTINCT ON (canonical_id) canonical_id, surface,
           1 - (embedding <=> $2::text::vector) AS score
    FROM dict_entry
    WHERE kind = $1 AND embedding IS NOT NULL
      AND 1 - (embedding <=> $2::text::vector) >= $3
    ORDER BY canonical_id, embedding <=> $2::text::vector, surface
) t
ORDER BY score DESC, canonical_id
LIMIT $4
"""

_PLACE_EXACT = """
SELECT id::text AS canonical_id, name_norm AS surface FROM location
WHERE name_norm = $1 AND lat IS NOT NULL
"""

# 기준 장소는 줄여 부르는 일이 많아('광안리' ↔ '광안리해수욕장') 낱말 유사도도 같이 본다
_PLACE_TRIGRAM = """
SELECT id::text AS canonical_id, name_norm AS surface,
       greatest(similarity(name_norm, $1), word_similarity($1, name_norm)) AS score
FROM location
WHERE lat IS NOT NULL
  AND greatest(similarity(name_norm, $1), word_similarity($1, name_norm)) >= $2
ORDER BY score DESC, length(name_norm), id
LIMIT $3
"""


def _vector_text(vec: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _matches(rows: Sequence[asyncpg.Record], via: str) -> list[Match]:
    return [
        Match(r["canonical_id"], r["surface"], float(r["score"]) if via != "exact" else 1.0, via)  # type: ignore[arg-type]
        for r in rows
    ]


async def lookup(
    conn: asyncpg.Connection,
    kind: Kind,
    text: str,
    limit: int = TOP_K,
    *,
    query_vec: Sequence[float] | None = None,
) -> list[Match]:
    """이름 하나의 후보(점수 높은 순). 정확히 같은 표기가 있으면 그것만(동명이면 모두) 돌려준다."""
    q = norm(text)
    if not q:
        return []
    if rows := await conn.fetch(_EXACT, kind, q):
        return _matches(rows, "exact")
    if rows := await conn.fetch(_TRIGRAM, kind, q, TRIGRAM_MIN, limit):
        return _matches(rows, "trigram")
    if query_vec is None:
        return []
    rows = await conn.fetch(_EMBEDDING, kind, _vector_text(query_vec), EMBEDDING_MIN, limit)
    return _matches(rows, "embedding")


async def has_embeddings(conn: asyncpg.Connection, kind: Kind) -> bool:
    """임베딩 보완을 할 수 있는지(그 종류에 embedding이 채워진 행이 있는지)."""
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM dict_entry WHERE kind = $1 AND embedding IS NOT NULL)", kind
    )


async def lookup_place(conn: asyncpg.Connection, text: str, limit: int = TOP_K) -> list[Match]:
    """기준 장소(anchor place) 후보: 좌표가 있는 촬영지를 이름으로 찾는다."""
    q = norm(text)
    if not q:
        return []
    if rows := await conn.fetch(_PLACE_EXACT, q):
        return _matches(rows, "exact")
    return _matches(await conn.fetch(_PLACE_TRIGRAM, q, TRIGRAM_MIN, limit), "trigram")


_DESCRIBE: dict[str, str] = {
    "work": "SELECT id::text AS id, title AS name, release_year::text AS detail"
    " FROM work WHERE id::text = ANY($1::text[])",
    # 동명이인 구분: 가장 최근 작품 제목
    "person": "SELECT p.id::text AS id, p.name,"
    " (SELECT w.title FROM work_person wp JOIN work w ON w.id = wp.work_id"
    "  WHERE wp.person_id = p.id ORDER BY w.release_year DESC NULLS LAST, w.id LIMIT 1) AS detail"
    " FROM person p WHERE p.id::text = ANY($1::text[])",
    "station": "SELECT id::text AS id, name, line AS detail"
    " FROM station WHERE id = ANY($1::text[])",
    "location": "SELECT id::text AS id, name, gu AS detail"
    " FROM location WHERE id::text = ANY($1::text[])",
}


async def describe(
    conn: asyncpg.Connection, kind: TargetKind, ids: Sequence[str]
) -> dict[str, Target]:
    """표준 ID → 이름. 질의문은 종류별로 정해 둔 것 중에서만 고른다."""
    rows = await conn.fetch(_DESCRIBE[kind], list(ids))
    return {r["id"]: Target(r["id"], r["name"], r["detail"]) for r in rows}

"""장소 사진 반영 (FR-207, BR-03, DATA-10). 맞추기 규칙은 app/batch/photos.py.

TourAPI 대표 이미지는 관리자가 편집하지 않는 값이라,
한 트랜잭션에서 DATA-10 사진을 지우고 다시 넣는다.
그래서 다시 돌려도 중복이 생기지 않고, 원본에서 빠진 사진은 함께 빠진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import asyncpg

SOURCE_ID = "DATA-10"
# db/init/06_photos.sql과 같은 행. 시드처럼 이미 만들어진 DB에도 출처가 있도록 반영 전에 넣는다
_SOURCE = (
    "INSERT INTO source (id, org, name, url, license, policy) VALUES"
    " ('DATA-10', '한국관광공사', 'TourAPI 관광지 대표 이미지',"
    " 'https://www.data.go.kr/data/15101578/openapi.do', '공공누리 제1유형(출처표시)', 'STORE')"
    " ON CONFLICT (id) DO NOTHING"
)


async def located(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """좌표가 있는 촬영지(id, name, lat, lon)."""
    return await conn.fetch("SELECT id, name, lat, lon FROM location WHERE lat IS NOT NULL")


async def replace(conn: asyncpg.Connection, rows: Sequence[tuple[int, str, str]]) -> int:
    """(location_id, url, title)로 DATA-10 사진을 바꾼다. 같은 (촬영지, 주소)는 한 번만. 넣은 수."""
    unique = list({(loc, url): (loc, url, title) for loc, url, title in rows}.values())
    async with conn.transaction():
        await conn.execute(_SOURCE)
        await conn.execute("DELETE FROM photo WHERE source_id = $1", SOURCE_ID)
        await conn.executemany(
            "INSERT INTO photo (location_id, url, kogl_type, title, source_id)"
            " VALUES ($1, $2, 1, $3, $4)",
            [(*r, SOURCE_ID) for r in unique],
        )
    return len(unique)

"""C1 배치 스테이징·반영 (ADR-012, FR-607·611). 스키마는 db/init/03_batch.sql.

- reset(): stg 테이블 비우기
- write_*(): 배치 결과를 stg에 쌓기
- publish(): 가드 통과 뒤 같은 트랜잭션에서 서비스 테이블로 반영
  (sync_record.guard_and_publish가 부른다).
  id는 정규화 이름으로 찾아 유지하고, 이번 수집에 없는 작품·촬영지는 지운다.
- 이전 반영 결과 읽기: 좌표 캐시(같은 주소면 다시 부르지 않음), 관리자 검수 결과
"""

import json
from collections.abc import Iterable

import asyncpg

STG_TABLES = ("stg.shooting", "stg.work", "stg.location", "stg.station")


async def reset(conn: asyncpg.Connection) -> None:
    await conn.execute("TRUNCATE " + ", ".join(STG_TABLES))


async def known_coords(conn: asyncpg.Connection) -> dict[str, tuple[str | None, float, float]]:
    """name_norm → (주소, 위도, 경도). 지난 반영에서 좌표를 얻은 촬영지."""
    rows = await conn.fetch(
        "SELECT name_norm, address, lat, lon FROM location WHERE lat IS NOT NULL"
    )
    return {r["name_norm"]: (r["address"], r["lat"], r["lon"]) for r in rows}


async def known_stations(conn: asyncpg.Connection) -> dict[str, tuple[float, float]]:
    rows = await conn.fetch("SELECT id, lat, lon FROM station")
    return {r["id"]: (r["lat"], r["lon"]) for r in rows}


async def review_decisions(conn: asyncpg.Connection) -> dict[str, tuple[str, str | None]]:
    """title_norm → (status, kmdb_id). 관리자가 처리한 매칭 검수(마지막 처리 기준)와
    대기 중인 것."""
    rows = await conn.fetch(
        """
        SELECT w.title_norm, r.status, r.proposal->>'kmdb_id' AS kmdb_id
        FROM review_item r JOIN work w ON w.id = r.target_id
        WHERE r.kind = 'match' AND r.target_type = 'work'
        ORDER BY r.handled_at NULLS FIRST, r.id
        """
    )
    return {r["title_norm"]: (r["status"], r["kmdb_id"]) for r in rows}


async def write_stations(conn: asyncpg.Connection, rows: Iterable[tuple]) -> None:
    await conn.copy_records_to_table(
        "station",
        schema_name="stg",
        records=list(rows),
        columns=["id", "name", "line", "lat", "lon", "gu"],
    )


async def write_works(conn: asyncpg.Connection, rows: Iterable[tuple]) -> None:
    records = [
        (
            *r[:4],
            json.dumps(r[4], ensure_ascii=False) if r[4] else None,
            *r[5:8],
            json.dumps(r[8], ensure_ascii=False) if r[8] else None,
            r[9],
        )
        for r in rows
    ]
    await conn.executemany(
        "INSERT INTO stg.work (title_norm, title, release_year, kmdb_id, kmdb, match_status,"
        " match_score, match_rule, candidate, source_id)"
        " VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10)",
        records,
    )


async def write_locations(conn: asyncpg.Connection, rows: Iterable[tuple]) -> None:
    await conn.copy_records_to_table(
        "location",
        schema_name="stg",
        records=list(rows),
        columns=[
            "name_norm",
            "name",
            "address",
            "gu",
            "lat",
            "lon",
            "geo_via",
            "nearest_station_id",
            "station_walk_min",
        ],
    )


async def write_shootings(conn: asyncpg.Connection, rows: Iterable[tuple]) -> None:
    await conn.executemany(
        "INSERT INTO stg.shooting (title_norm, name_norm, source_id, raw, base_date)"
        " VALUES ($1,$2,$3,$4::jsonb,$5)",
        [(a, b, c, json.dumps(d, ensure_ascii=False), e) for a, b, c, d, e in rows],
    )


async def publish(conn: asyncpg.Connection) -> None:
    """stg → 서비스 테이블. 호출하는 쪽이 트랜잭션을 연다."""
    await conn.execute(
        """
        INSERT INTO station (id, name, line, lat, lon, gu)
        SELECT id, name, line, lat, lon, gu FROM stg.station
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, line = EXCLUDED.line,
            lat = EXCLUDED.lat, lon = EXCLUDED.lon, gu = EXCLUDED.gu
        """
    )
    # kmdb_id는 UNIQUE라 작품 사이에서 옮겨 갈 수 있게 먼저 비운다(같은 트랜잭션 안)
    await conn.execute("UPDATE work SET kmdb_id = NULL WHERE kmdb_id IS NOT NULL")
    await conn.execute(
        """
        INSERT INTO work (title, title_norm, release_year, kmdb_id, kmdb,
                          match_status, match_score, source_id)
        SELECT title, title_norm, release_year, kmdb_id, kmdb,
               match_status, match_score, source_id
        FROM stg.work
        ON CONFLICT (title_norm) DO UPDATE SET
            title = EXCLUDED.title, release_year = EXCLUDED.release_year,
            kmdb_id = EXCLUDED.kmdb_id, kmdb = EXCLUDED.kmdb,
            match_status = EXCLUDED.match_status, match_score = EXCLUDED.match_score,
            source_id = EXCLUDED.source_id
        """
    )
    await conn.execute(
        "DELETE FROM work w"
        " WHERE NOT EXISTS (SELECT 1 FROM stg.work s WHERE s.title_norm = w.title_norm)"
    )
    await conn.execute(
        """
        INSERT INTO location (name, name_norm, address, gu, lat, lon,
                              nearest_station_id, station_walk_min)
        SELECT name, name_norm, address, gu, lat, lon, nearest_station_id, station_walk_min
        FROM stg.location
        ON CONFLICT (name_norm) DO UPDATE SET
            name = EXCLUDED.name, address = EXCLUDED.address, gu = EXCLUDED.gu,
            lat = EXCLUDED.lat, lon = EXCLUDED.lon,
            nearest_station_id = EXCLUDED.nearest_station_id,
            station_walk_min = EXCLUDED.station_walk_min
        """
    )
    await conn.execute(
        "DELETE FROM location l"
        " WHERE NOT EXISTS (SELECT 1 FROM stg.location s WHERE s.name_norm = l.name_norm)"
    )
    await conn.execute("DELETE FROM shooting")
    await conn.execute(
        """
        INSERT INTO shooting (work_id, location_id, source_id, raw, base_date)
        SELECT w.id, l.id, s.source_id, s.raw, s.base_date
        FROM stg.shooting s
        JOIN work w ON w.title_norm = s.title_norm
        JOIN location l ON l.name_norm = s.name_norm
        """
    )
    # 이번 판정에서 검수 대기가 아니게 된 작품의 '대기 중' 매칭 검수는 지운다(처리된 기록은 남김)
    await conn.execute(
        """
        DELETE FROM review_item r
        USING work w JOIN stg.work s ON s.title_norm = w.title_norm
        WHERE r.kind = 'match' AND r.target_type = 'work' AND r.target_id = w.id
          AND r.status = 'pending' AND s.match_status <> 'review'
        """
    )
    # 검수 대기 작품: 이미 큐에 있거나 처리된 작품은 다시 넣지 않는다
    await conn.execute(
        """
        INSERT INTO review_item (kind, target_type, target_id, proposal, proposer, score)
        SELECT 'match', 'work', w.id, s.candidate, 'rule', s.match_score
        FROM stg.work s JOIN work w ON w.title_norm = s.title_norm
        WHERE s.match_status = 'review' AND s.candidate IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM review_item r
                          WHERE r.kind = 'match' AND r.target_type = 'work' AND r.target_id = w.id)
        """
    )

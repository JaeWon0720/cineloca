"""C1 배치 스테이징·반영 (ADR-012, FR-607·611). 스키마는 db/init/03_batch.sql.

- reset(): stg 테이블 비우기
- write_*(): 배치 결과를 stg에 쌓기
- publish(): 가드 통과 뒤 같은 트랜잭션에서 서비스 테이블로 반영
  (sync_record.guard_and_publish가 부른다).
  id는 정규화 이름으로 찾아 유지하고, 이번 수집에 없는 작품·촬영지는 지운다.
  끝에 고유명사 사전(dict_entry, FR-303)을 새 작품·역에 맞춘다.
- 이전 반영 결과 읽기: 좌표 캐시(같은 주소면 다시 부르지 않음), 관리자 검수 결과
"""

import json
import logging
from collections.abc import Iterable

import asyncpg

from app.data import dictionary

logger = logging.getLogger(__name__)

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
    대기 중인 것. 규칙 항목만 읽는다(AI 제안 #41은 참고용이라 결정을 덮지 않음)."""
    rows = await conn.fetch(
        """
        SELECT w.title_norm, r.status, r.proposal->>'kmdb_id' AS kmdb_id
        FROM review_item r JOIN work w ON w.id = r.target_id
        WHERE r.kind = 'match' AND r.target_type = 'work' AND r.proposer = 'rule'
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
    # 코스(course·course_stop)가 쓰는 촬영지는 지우지 않는다(외래 키 오류로 반영 전체 실패 방지).
    # 남긴 곳은 촬영 기록이 없어지므로 관리자가 코스를 고쳐야 한다.
    await conn.execute(
        """
        DELETE FROM location l
        WHERE NOT EXISTS (SELECT 1 FROM stg.location s WHERE s.name_norm = l.name_norm)
          AND NOT EXISTS (SELECT 1 FROM course_stop cs WHERE cs.location_id = l.id)
          AND NOT EXISTS (SELECT 1 FROM course c WHERE c.start_location_id = l.id)
        """
    )
    kept = await conn.fetchval(
        "SELECT count(*) FROM location l"
        " WHERE NOT EXISTS (SELECT 1 FROM stg.location s WHERE s.name_norm = l.name_norm)"
    )
    if kept:
        logger.warning("원본에서 빠졌지만 코스가 쓰고 있어 남긴 촬영지 %d곳", kept)
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
          AND r.proposer = 'rule'
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
                          WHERE r.kind = 'match' AND r.target_type = 'work' AND r.target_id = w.id
                            AND r.proposer = 'rule')
        """
    )
    # F3 사전 대조(FR-303)가 새 작품·역 이름을 바로 찾게 사전을 같은 트랜잭션에서 맞춘다
    await dictionary.rebuild(conn)

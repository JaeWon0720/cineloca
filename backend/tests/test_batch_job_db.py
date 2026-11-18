"""C1 배치 한 번 전체 DB 테스트 (FR-601~607·611, #25). 외부 API는 가짜 응답만 쓴다.

스키마(db/init 01~03)를 적재한 '테스트 전용' DB가 있을 때만 돈다. 작품·촬영지를 비우므로
개발 DB를 쓰면 안 된다.
    CINELOCA_TEST_DATABASE_URL=postgresql://... python -m pytest tests/test_batch_job_db.py
"""

import asyncio
import os

import asyncpg
import httpx
import pytest

from app.batch import guard, job, kmdb
from app.batch.geocode import GeoResult

DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")

ROWS = {
    "getBfcMovieBusan": [
        {"opusNm": "해운대", "plcNm": "광복로", "roadNmAddr": "부산 중구 광복로 55",
         "opnDay": "2009-07-22"},
        {"opusNm": "해운대", "plcNm": "청사포", "roadNmAddr": "부산 해운대구 중동"},
    ],
    "getCineMap": [{"opusNm": "변호인", "plcNm": "흰여울문화마을"}],
    "getStationInfo": [
        {"scode": "110", "sname": "남포", "saddr": "부산 중구 구덕로 지하 12"},
        {"scode": "X1", "sname": "코드 없음"},
    ],
}  # fmt: skip


def data_go_kr(req: httpx.Request) -> httpx.Response:
    items = ROWS[req.url.path.rsplit("/", 1)[1]]
    body = {"response": {"body": {"totalCount": len(items), "items": {"item": items}}}}
    return httpx.Response(200, json=body)


class Juso:
    def __init__(self) -> None:
        self.calls = 0

    async def geocode(self, address: str) -> GeoResult | None:
        self.calls += 1
        return GeoResult(35.0985, 129.0347, "address")


class Kakao:
    async def geocode(self, name: str, address: str | None) -> GeoResult | None:
        return GeoResult(35.161, 129.191, "keyword") if name == "청사포" else None


class Kmdb:
    has_location = True

    async def candidates(self, title: str) -> list[kmdb.Candidate]:
        return [kmdb.Candidate(docid="K1", title="해운대", rel_date="20090722", plot="p")]


def test_run_publishes_and_second_run_reuses_address_coords(monkeypatch) -> None:
    monkeypatch.setattr(guard, "MIN_WORKS", 1)  # 건수 가드 하한은 실제 규모 기준
    monkeypatch.setattr(guard, "MIN_LOCATIONS", 1)

    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(
                "TRUNCATE review_item, shooting, work, location, station, sync_run"
                " RESTART IDENTITY CASCADE"
            )
            juso = Juso()
            async with httpx.AsyncClient(transport=httpx.MockTransport(data_go_kr)) as http:
                c = job.Clients(http, "D", juso, Kakao(), Kmdb())
                run_id, counts, stats = await job.run(conn, c)
                assert await conn.fetchval("SELECT status FROM sync_run WHERE id=$1", run_id) == (
                    "succeeded"
                )
                assert counts.fetched == {"DATA-01": 2, "DATA-02": 1, "DATA-08": 1}
                assert counts.geo.by_source == {"address": 1, "keyword": 1, "none": 1}
                assert (counts.match.matched, counts.match.unmatched) == (1, 1)
                assert stats.match_rule["score"] == 1 and juso.calls == 2  # 역 1 + 촬영지 1

                loc = await conn.fetchrow(
                    "SELECT lat, nearest_station_id, station_walk_min FROM location"
                    " WHERE name_norm='광복로'"
                )
                assert loc["nearest_station_id"] == "110" and loc["station_walk_min"] < 1
                assert await conn.fetchval("SELECT kmdb->>'plot' FROM work WHERE kmdb_id='K1'")
                assert await conn.fetchval("SELECT count(*) FROM shooting") == 3

                await job.run(conn, c)  # 2회차: 같은 주소·역은 다시 부르지 않는다
                assert juso.calls == 2
                assert await conn.fetchval(
                    "SELECT lat FROM location WHERE name_norm='청사포'"
                ) == pytest.approx(35.161)
        finally:
            await conn.close()

    asyncio.run(main())

"""API-019 데이터 출처 테스트 (FR-705, UI-09)."""

import asyncio
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.api.routers.sources import get_ledger
from app.core.config import Settings
from app.core.notice import NOTICE_VERSION
from app.core.sources import LICENSES, RUNTIME_SOURCES
from app.main import create_app
from app.services import amenity_recommend, archive_service, template_searcher
from app.services.source_cards import ledger

ROOT = Path(__file__).resolve().parents[2]
DSN = os.environ.get("CINELOCA_TEST_DATABASE_URL")
STORED = {
    "id": "DATA-01",
    "provider": "부산광역시(부산영상위원회)",
    "name": "영화 속 부산정보",
    "base_date": "2026-11-18",
    "url": "https://www.data.go.kr/data/15164328/openapi.do",
    "license": "공공데이터 이용허락범위 제한 없음",
    "policy": "STORE",
}


def _client(rows: list[dict]) -> TestClient:
    app = create_app(Settings(database_url=None))

    async def load() -> list[dict]:
        return rows

    app.dependency_overrides[get_ledger] = lambda: load
    return TestClient(app)


def test_sources_shape_matches_screen_type() -> None:
    res = _client([STORED, *RUNTIME_SOURCES]).get("/api/sources")  # 로그인 없이 본다

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"ledger", "notice_version", "licenses"}  # SourcesInfo
    assert body["notice_version"] == NOTICE_VERSION
    assert body["ledger"][0] == STORED
    assert [s["policy"] for s in body["ledger"][1:]] == ["NO_STORE", "NO_STORE"]
    assert len(body["licenses"]) == len(LICENSES)
    assert all(set(x) == {"name", "license"} and x["license"] for x in body["licenses"])


def _init_source_ids() -> set[str]:
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "db/init").glob("*.sql")))
    return set(re.findall(r"\('(DATA-\d+)',", sql))


def test_every_source_id_used_by_services_is_in_ledger() -> None:
    """결과 카드에 붙는 출처 ID는 모두 출처 화면 대장에 있어야 한다(FR-704·705)."""
    used = {
        archive_service.KMDB_SOURCE,
        template_searcher.GEO_SOURCE,
        template_searcher.STATION_SOURCE,
        amenity_recommend.TOURAPI_SOURCE["id"],
        "DATA-01",
        "DATA-02",
        "DATA-09",
    }
    runtime = {s["id"] for s in RUNTIME_SOURCES}
    assert used <= _init_source_ids() | runtime
    assert not runtime & _init_source_ids()  # 저장 출처와 겹치지 않는다


@asynccontextmanager
async def _borrow(conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    yield conn


@pytest.mark.skipif(not DSN, reason="CINELOCA_TEST_DATABASE_URL 없음")
def test_ledger_reads_source_table_then_runtime_sources() -> None:
    async def main() -> None:
        conn = await asyncpg.connect(DSN)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("TRUNCATE source RESTART IDENTITY CASCADE")
            await conn.execute(
                "INSERT INTO source (id, org, name, url, license, policy, base_date) VALUES"
                " ('DATA-04', '한국영상자료원', 'KMDb', 'https://example.org/4', '공공누리 1유형',"
                " 'STORE', NULL), ('DATA-07', '카카오', '지도', 'https://example.org/7', '약관',"
                " 'NO_STORE', '2026-11-18')"
            )
            rows = await ledger(connect=lambda: _borrow(conn))
        finally:
            await tx.rollback()
            await conn.close()
        assert [r["id"] for r in rows] == ["DATA-04", "DATA-07", "DATA-13"]  # 테이블 값이 먼저
        assert rows[1]["name"] == "지도" and str(rows[1]["base_date"]) == "2026-11-18"
        assert rows[0]["provider"] == "한국영상자료원" and rows[0]["policy"] == "STORE"

    asyncio.run(main())

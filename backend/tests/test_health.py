"""API-025 상태 확인 테스트 (TC-N02 준비). 실제 DB 없이 점검 결과만 바꿔 끼워 시험한다."""

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routers.health import get_database_checker
from app.core.config import Settings
from app.data import db
from app.main import app


def _use_database_result(result: bool) -> None:
    async def fake_check() -> bool:
        return result

    app.dependency_overrides[get_database_checker] = lambda: fake_check


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_ok_when_database_responds(client: TestClient) -> None:
    _use_database_result(True)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "checks": {"db": "ok"}}


def test_health_degraded_when_database_down(client: TestClient) -> None:
    _use_database_result(False)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "version": "0.1.0", "checks": {"db": "error"}}


def test_openapi_lists_health_endpoint(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/health" in response.json()["paths"]


def test_ping_is_false_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "get_settings", lambda: Settings(database_url=None))
    monkeypatch.setattr(db, "_pool", None)

    assert asyncio.run(db.ping()) is False

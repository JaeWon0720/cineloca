"""F2 API 계층 테스트 (API-005~007, FR-201~210). DB 없이 가짜 MapService로 형식·오류만 본다.

실제 조회는 test_map_service_db.py(테스트 DB가 있을 때)에서 확인한다.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.api.routers.locations import get_map_service
from app.data.db import DatabaseUnavailable
from app.main import create_app
from app.services.map_service import MapService
from tests.f2_fake import FakeMapService

HAEUNDAE = {"lat": 35.1587, "lon": 129.1604}


@pytest.fixture
def fake() -> FakeMapService:
    return FakeMapService()


@pytest.fixture
def client(fake: FakeMapService) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_map_service] = lambda: fake
    yield TestClient(app)


def test_search_passes_conditions_and_validates_response(
    client: TestClient, fake: FakeMapService
) -> None:
    res = client.post(
        "/api/locations/search",
        json={"base": HAEUNDAE, "genres": ["재난"], "year_from": 2000, "gu": ["해운대구"]},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["radius_m"] == 1000 and body["total"] == 1  # 기본 반경(FR-203)
    assert body["items"][0]["work_count"] == 4 and len(body["items"][0]["works"]) == 3
    assert {s["id"] for s in body["sources"]} == {"DATA-01"}  # 출처 카드(FR-704)
    assert fake.calls == [
        {
            "lat": 35.1587,
            "lon": 129.1604,
            "radius_m": 1000,
            "genres": ["재난"],  # 묶음 이름 그대로 넘기고 KMDb 장르로 펼치는 일은 서비스가 한다
            "year_from": 2000,
            "year_to": None,
            "gu": ["해운대구"],
        }
    ]


def test_search_does_not_echo_base_location(client: TestClient) -> None:
    res = client.post("/api/locations/search", json={"base": {"lat": 35.15871, "lon": 129.16042}})

    assert "35.15871" not in res.text and "129.16042" not in res.text  # NFR-003


@pytest.mark.parametrize(
    "payload",
    [
        {"base": HAEUNDAE, "radius_m": 700},
        {"base": {"lat": 135, "lon": 129}},
        {"base": HAEUNDAE, "gu": ["강남구"]},
        {"base": HAEUNDAE, "year_from": 2020, "year_to": 2010},
        {"base": HAEUNDAE, "genres": ["코메디"]},  # KMDb 원래 장르명이 아니라 묶음 이름으로 받는다
        {"radius_m": 1000},
    ],
)
def test_search_rejects_invalid_request(
    client: TestClient, fake: FakeMapService, payload: dict
) -> None:
    res = client.post("/api/locations/search", json=payload)

    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "135" not in error["message"]  # 입력값을 되돌려 주지 않음
    assert fake.calls == []


def test_location_without_coordinates(client: TestClient) -> None:
    body = client.get("/api/locations/4").json()

    assert body["located"] is False
    assert body["lat"] is None and body["directions_url"] is None  # FR-210
    assert body["caution"] == "residential" and body["caution_notice"]  # FR-209
    assert body["cert"]["eligible"] is False  # FR-506
    assert body["works"][0]["kmdb_matched"] is True


def test_location_not_found_uses_error_format(client: TestClient) -> None:
    res = client.get("/api/locations/999999")

    assert res.status_code == 404
    assert res.json()["error"] == {
        "code": "LOCATION_NOT_FOUND",
        "message": "촬영지를 찾을 수 없습니다",
        "retry_after": None,
    }


def test_unknown_path_uses_error_format(client: TestClient) -> None:
    res = client.get("/api/nope")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_meta(client: TestClient) -> None:
    body = client.get("/api/meta").json()

    assert body["default_base"]["label"] == "부산역"  # FR-201
    assert len(body["genres"]) == 12 and body["year_range"] == {"min": 2000, "max": 2025}


def test_database_down_returns_503() -> None:
    @asynccontextmanager
    async def down() -> AsyncIterator[None]:
        raise DatabaseUnavailable
        yield

    app = create_app()
    app.dependency_overrides[get_map_service] = lambda: MapService(connect=down)  # type: ignore[arg-type]
    client = TestClient(app)

    for res in (
        client.post("/api/locations/search", json={"base": HAEUNDAE}),
        client.get("/api/locations/1"),
        client.get("/api/meta"),
    ):
        assert res.status_code == 503  # ERR-015
        assert res.json()["error"]["code"] == "DB_UNAVAILABLE"


def test_openapi_lists_f2_endpoints() -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert "/api/locations/search" in paths and "post" in paths["/api/locations/search"]
    assert "/api/locations/{location_id}" in paths
    assert "503" in paths["/api/meta"]["get"]["responses"]

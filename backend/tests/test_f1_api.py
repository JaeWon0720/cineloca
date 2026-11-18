"""F1 API 계층 테스트 (API-001·002, FR-101~106). DB 없이 가짜 서비스로 형식·오류만 본다.

실제 조회는 test_archive_service_db.py(테스트 DB가 있을 때)에서 확인한다.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routers.works import get_archive_service
from app.main import create_app

SOURCE = {
    "id": "DATA-01",
    "provider": "부산영상위원회",
    "name": "영화 속 부산정보",
    "base_date": None,
    "url": "https://www.data.go.kr/data/15164328/openapi.do",
}
SUMMARY = {
    "id": 11,
    "title": "해운대",
    "release_year": 2009,
    "genres": ["드라마", "재난"],
    "director": "윤제균",
    "location_count": 1,
    "kmdb_matched": True,
}
DETAIL = {
    **SUMMARY,
    "cast": ["설경구", "하지원"],
    "synopsis": "가짜 줄거리",
    "trailer_url": None,
    "kmdb_url": None,
    "locations": [
        {"id": 1, "name": "해운대", "gu": "해운대구", "located": True, "lat": 35.16, "lon": 129.16},
        {"id": 2, "name": "어느 골목", "gu": None, "located": False, "lat": None, "lon": None},
    ],
    "related": [{"id": 12, "title": "도마뱀", "release_year": 2006}],
    "sources": [SOURCE],
}  # fmt: skip


class FakeArchiveService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_works(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"total": 1, "page": kwargs["page"], "size": kwargs["size"], "items": [SUMMARY],
                "sources": [SOURCE]}  # fmt: skip

    async def get_work(self, work_id: int) -> dict[str, Any] | None:
        return DETAIL if work_id == 11 else None


@pytest.fixture
def fake() -> FakeArchiveService:
    return FakeArchiveService()


@pytest.fixture
def client(fake: FakeArchiveService) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_archive_service] = lambda: fake
    yield TestClient(app)


def test_list_defaults_and_conditions(client: TestClient, fake: FakeArchiveService) -> None:
    body = client.get("/api/works").json()
    client.get("/api/works", params={"q": "하정우", "genre": "범죄", "sort": "year", "page": 2})

    assert body["size"] == 20 and body["page"] == 1  # FR-101: 한 쪽에 20편
    assert body["items"][0]["location_count"] == 1 and body["sources"][0]["id"] == "DATA-01"
    assert fake.calls[0] == {"q": None, "genre": None, "sort": "title", "page": 1, "size": 20}
    assert fake.calls[1] == {"q": "하정우", "genre": "범죄", "sort": "year", "page": 2, "size": 20}


@pytest.mark.parametrize(
    "params", [{"sort": "rating"}, {"page": 0}, {"size": 101}, {"q": "가" * 51}]
)
def test_list_rejects_invalid_request(client: TestClient, params: dict) -> None:
    res = client.get("/api/works", params=params)

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_REQUEST"


def test_detail_shape(client: TestClient) -> None:
    body = client.get("/api/works/11").json()

    assert body["title"] == "해운대" and body["cast"] == ["설경구", "하지원"]
    assert [loc["located"] for loc in body["locations"]] == [True, False]  # FR-210
    assert body["related"][0]["id"] == 12 and body["sources"]
    assert "rating" not in body  # 등급은 API-003으로 따로 받는다


def test_unknown_work_uses_error_format(client: TestClient) -> None:
    res = client.get("/api/works/999")

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "WORK_NOT_FOUND"


def test_openapi_lists_f1_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert {"/api/works", "/api/works/{work_id}"} <= set(paths)

"""API-026 인물 후보 API 계층 테스트(FR-310 필터 패널). DB 없이 가짜 서비스로 형식·검증만 본다.

실제 이름 찾기는 test_map_service_db.py(테스트 DB가 있을 때)에서 확인한다.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routers.locations import get_map_service
from app.main import create_app


class FakePersons:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def persons(self, q: str, limit: int = 10) -> list[dict[str, Any]]:
        self.calls.append((q, limit))
        return [
            {"id": 1, "name": "송강호", "work": "변호인"},
            {"id": 2, "name": "송강호", "work": None},
        ]


@pytest.fixture
def fake() -> FakePersons:
    return FakePersons()


@pytest.fixture
def client(fake: FakePersons) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_map_service] = lambda: fake
    yield TestClient(app)


def test_persons_returns_candidates(client: TestClient, fake: FakePersons) -> None:
    res = client.get("/api/persons", params={"q": "송강호", "limit": 5})

    assert res.status_code == 200
    assert res.json() == {
        "items": [
            {"id": 1, "name": "송강호", "work": "변호인"},
            {"id": 2, "name": "송강호", "work": None},
        ]
    }
    assert fake.calls == [("송강호", 5)]


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "가" * 31}, {"q": "송", "limit": 0}])
def test_persons_validates_query(client: TestClient, params: dict[str, Any]) -> None:
    res = client.get("/api/persons", params=params)

    assert res.status_code == 422

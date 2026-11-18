"""F5 API 계층 테스트 (API-016~018, ERR-008~010, NFR-003). DB 대신 가짜 저장소를 쓴다."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers import stamps as stamps_router
from app.api.routers.stamps import get_stamp_service
from app.core.config import Settings
from app.main import create_app
from app.services.stamp_service import StampService
from tests.test_stamp_service import LAT, LON, SIGNER, fake_repos, no_db, north  # noqa: F401

SECRET_LAT = round(north(30), 7)  # 응답·로그에 나오면 안 되는 좌표


def _app(signer: object = SIGNER, log_dir: Path | None = None) -> FastAPI:
    app = create_app(Settings(database_url=None, log_dir=str(log_dir) if log_dir else None))
    app.dependency_overrides[get_stamp_service] = lambda: StampService(signer, connect=no_db)  # type: ignore[arg-type]
    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(_app())


def body(loc: int = 1, lat: float = SECRET_LAT, accuracy: float = 10, held: list | None = None):
    return {"location_id": loc, "lat": lat, "lon": LON, "accuracy_m": accuracy, "held": held or []}


def test_certify_success_and_failures(client: TestClient) -> None:
    ok = client.post("/api/stamps", json=body()).json()
    far = client.post("/api/stamps", json=body(lat=north(150))).json()
    blurry = client.post("/api/stamps", json=body(accuracy=80)).json()
    again = client.post("/api/stamps", json=body(held=[ok["token"]])).json()

    assert ok["ok"] is True and ok["distance_m"] == 30 and ok["reason"] is None
    assert far == {"ok": False, "token": None, "reason": "out_of_range", "distance_m": 150}
    assert blurry == {"ok": False, "token": None, "reason": "low_accuracy", "distance_m": None}
    assert again["reason"] == "already_certified"  # FR-505


def test_location_is_not_echoed_or_logged(tmp_path: Path) -> None:
    client = TestClient(_app(log_dir=tmp_path))

    res = client.post("/api/stamps", json=body())
    bad = client.post("/api/stamps", json=body(accuracy=-1))

    log = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    for text in (res.text, bad.text, log):
        assert str(SECRET_LAT) not in text and str(LON) not in text  # NFR-003, BR-10
    assert '"path":"/api/stamps"' in log


@pytest.mark.parametrize(
    ("loc", "status", "code", "word"),
    [
        (3, 409, "CERT_EXCLUDED", "주민"),  # ERR-009
        (4, 409, "CERT_EXCLUDED", "위치가 확인되지"),
        (5, 409, "CERT_EXCLUDED", "제외"),
        (999, 404, "LOCATION_NOT_FOUND", "찾을 수 없"),
    ],
)
def test_certify_errors(client: TestClient, loc: int, status: int, code: str, word: str) -> None:
    res = client.post("/api/stamps", json=body(loc))

    assert res.status_code == status
    assert res.json()["error"]["code"] == code and word in res.json()["error"]["message"]


@pytest.mark.parametrize(
    "payload",
    [
        {"location_id": 1, "lat": LAT, "lon": LON},  # 정확도 없음
        {"location_id": 1, "lat": 135, "lon": LON, "accuracy_m": 5},
        {"location_id": 0, "lat": LAT, "lon": LON, "accuracy_m": 5},
        {"location_id": 1, "lat": LAT, "lon": LON, "accuracy_m": -1},
        {"location_id": 1, "lat": LAT, "lon": LON, "accuracy_m": 5, "held": ["t"] * 2001},
    ],
)
def test_certify_rejects_invalid_request(client: TestClient, payload: dict) -> None:
    res = client.post("/api/stamps", json=payload)

    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_key_returns_503() -> None:
    client = TestClient(_app(signer=None))

    for res in (
        client.post("/api/stamps", json=body()),
        client.post("/api/stamps/verify", json={"tokens": []}),
        client.post("/api/badges", json={"course": {"id": 9}, "stamps": []}),
    ):
        assert res.status_code == 503 and res.json()["error"]["code"] == "STAMP_UNAVAILABLE"


def test_default_dependency_without_key_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stamps_router, "get_settings", lambda: Settings(database_url=None))

    res = TestClient(create_app()).post("/api/stamps/verify", json={"tokens": []})

    assert res.status_code == 503


def test_verify_and_badge(client: TestClient) -> None:
    s1, s2 = (client.post("/api/stamps", json=body(i)).json()["token"] for i in (1, 2))

    book = client.post("/api/stamps/verify", json={"tokens": [s1, s2, "x.y.z"]}).json()
    partial = client.post("/api/badges", json={"course": {"id": 9}, "stamps": [s1]}).json()
    badge = client.post("/api/badges", json={"course": {"id": 9}, "stamps": [s1, s2]}).json()
    saved = client.post(
        "/api/badges", json={"course": {"location_ids": [1, 2]}, "stamps": [s1, s2]}
    ).json()
    missing = client.post("/api/badges", json={"course": {"id": 1}, "stamps": []})

    assert [v["location"]["name"] for v in book["valid"]] == ["장소1", "장소2"]
    assert book["invalid_count"] == 1 and book["badges"] == []
    assert (partial["ok"], partial["reason"], partial["missing"]) == (False, "missing_stamps", [2])
    assert badge["ok"] and badge["course_key"] == "course:9" and badge["token"]
    assert saved["ok"] and saved["course_key"].startswith("saved:")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "COURSE_NOT_FOUND"


@pytest.mark.parametrize(
    "course",
    [
        {},
        {"id": 9, "location_ids": [1, 2]},
        {"location_ids": []},
        {"location_ids": list(range(1, 32))},
    ],
)
def test_badge_rejects_invalid_course(client: TestClient, course: dict) -> None:
    res = client.post("/api/badges", json={"course": course, "stamps": []})

    assert res.status_code == 422


def test_openapi_lists_f5_endpoints() -> None:
    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]

    assert {"/api/stamps", "/api/stamps/verify", "/api/badges"} <= set(paths)
    assert "409" in paths["/api/stamps"]["post"]["responses"]

"""API-011 나만의 코스 경로 테스트 (FR-403~405·413, BR-10·12·13). DB 대신 가짜 저장소를 쓴다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.courses import get_course_service
from app.core.config import Settings
from app.data import locations as location_repo
from app.data import stations as station_repo
from app.data.locations import CertTarget
from app.data.stations import Network, StationPoint
from app.main import create_app
from app.services.course_service import CourseService

M = 1 / 111_195
LAT, LON = 35.10, 129.03
SECRET = round(LAT - 123 * M, 7)  # 현재 위치(응답·로그에 나오면 안 됨)


def place(i: int, north_m: float, **kw: object) -> CertTarget:
    base = {"caution": None, "cert_radius_m": 100, "cert_excluded": False, "gu": "중구"}
    base.update(kw)
    return CertTarget(i, f"장소{i}", LAT + north_m * M, LON, **base)  # type: ignore[arg-type]


PLACES = {
    1: place(1, 300),
    2: place(2, 900),
    3: place(3, 600, caution="school"),
    4: CertTarget(4, "좌표없음", None, None, None, 100, False),
    5: place(5, 9000),
    6: place(6, 450, gu="이상한구"),
}
STATION = StationPoint("101", "에이", "1호선", LAT, LON)
NETWORK = Network(
    [STATION, StationPoint("103", "씨", "1호선", LAT + 9000 * M, LON)],
    {("101", "103"): 10.0, ("103", "101"): 10.0},
)


@pytest.fixture(autouse=True)
def fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cert_targets(_c: object, ids: list[int]) -> dict[int, CertTarget]:
        return {i: PLACES[i] for i in ids if i in PLACES}

    async def get_station(_c: object, sid: str) -> StationPoint | None:
        return {"101": STATION}.get(sid)

    monkeypatch.setattr(location_repo, "cert_targets", cert_targets)
    monkeypatch.setattr(station_repo, "get_station", get_station)


@asynccontextmanager
async def no_db() -> AsyncIterator[None]:
    yield None


async def network() -> Network:
    return NETWORK


def _app(log_dir: Path | None = None) -> FastAPI:
    app = create_app(Settings(database_url=None, log_dir=str(log_dir) if log_dir else None))
    app.dependency_overrides[get_course_service] = lambda: CourseService(no_db, network)  # type: ignore[arg-type]
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


def plan(client: TestClient, start: dict, ids: list[int], fixed: bool = False):
    return client.post(
        "/api/courses/plan", json={"start": start, "location_ids": ids, "fixed_order": fixed}
    )


CURRENT = {"type": "current", "lat": SECRET, "lon": LON}


def test_plan_from_current_location_orders_and_totals(client: TestClient) -> None:
    body = plan(client, CURRENT, [2, 1, 3]).json()

    assert [s["id"] for s in body["stops"]] == [1, 3, 2]  # 300 → 600 → 900m
    assert body["start"] == {"type": "current", "label": "현재 위치", "lat": None, "lon": None}
    assert [(leg["from_order"], leg["to_order"]) for leg in body["legs"]] == [
        (0, 1),
        (1, 2),
        (2, 3),
    ]
    assert all(leg["mode"] == "walk" for leg in body["legs"])
    assert body["stay_min"] == 60 and body["total_min"] == body["travel_min"] + 60  # BR-13
    assert body["total_distance_m"] == sum(leg["distance_m"] for leg in body["legs"])
    assert body["stops"][1]["cert"]["excluded_reason"] == "school"  # 인증 제외 표시
    assert body["optimized"] is True and body["subway_data"] is True


def test_fixed_order_keeps_given_order(client: TestClient) -> None:
    body = plan(client, CURRENT, [2, 1, 3], fixed=True).json()

    assert [s["id"] for s in body["stops"]] == [2, 1, 3] and body["optimized"] is False


def test_start_at_station_and_subway_leg(client: TestClient) -> None:
    body = plan(client, {"type": "station", "id": "101"}, [1, 5]).json()

    assert body["start"]["label"] == "에이역(1호선)"
    subway = [leg for leg in body["legs"] if leg["mode"] == "subway"]
    assert len(subway) == 1 and subway[0]["board"]["id"] == "101"
    assert subway[0]["alight"]["id"] == "103" and subway[0]["ride_min"] == 10


def test_start_at_location_is_first_stop(client: TestClient) -> None:
    body = plan(client, {"type": "location", "id": "2"}, [1, 3]).json()

    assert body["stops"][0]["id"] == 2 and body["start"]["label"] == "장소2"
    assert body["legs"][0]["from_order"] == 1  # 촬영지 출발이면 0번(출발지) 구간이 없다
    assert len(body["legs"]) == 2


def test_unknown_gu_is_blanked(client: TestClient) -> None:
    body = plan(client, CURRENT, [1, 6]).json()

    assert {s["id"]: s["gu"] for s in body["stops"]}[6] is None


def test_current_location_is_not_echoed_or_logged(tmp_path: Path) -> None:
    res = plan(TestClient(_app(tmp_path)), CURRENT, [1, 2])

    log = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    assert res.status_code == 200
    assert str(SECRET) not in res.text and str(SECRET) not in log  # BR-10


@pytest.mark.parametrize(
    ("start", "ids", "status", "code"),
    [
        (CURRENT, [1, 4], 409, "LOCATION_NOT_LOCATED"),
        (CURRENT, [1, 999], 404, "LOCATION_NOT_FOUND"),
        ({"type": "station", "id": "777"}, [1, 2], 404, "STATION_NOT_FOUND"),
        (CURRENT, [1], 422, "INVALID_REQUEST"),  # 2곳 미만
        ({"type": "location", "id": "1"}, [1], 422, "INVALID_REQUEST"),
        (CURRENT, list(range(1, 12)), 422, "INVALID_REQUEST"),
        ({"type": "current", "lat": 35.1}, [1, 2], 422, "INVALID_REQUEST"),
        (
            {"type": "station", "id": "101", "lat": 35.1, "lon": 129.0},
            [1, 2],
            422,
            "INVALID_REQUEST",
        ),
        ({"type": "location", "id": "abc"}, [1, 2], 422, "INVALID_REQUEST"),
    ],
)
def test_errors(client: TestClient, start: dict, ids: list[int], status: int, code: str) -> None:
    res = plan(client, start, ids)

    assert res.status_code == status and res.json()["error"]["code"] == code
    assert str(SECRET) not in res.text

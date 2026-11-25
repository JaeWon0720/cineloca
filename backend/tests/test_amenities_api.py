"""API-012 코스 주변 시설 추천 테스트 (FR-414~418, FR-701·707). DB·TourAPI·AI 대신 가짜를 쓴다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routers.amenities import get_amenity_service
from app.core.config import Settings
from app.core.notice import NOTICE_VERSION
from app.data import locations as location_repo
from app.data.ai_gateway import DefaultPromptBuilder, ProviderChain
from app.data.ai_gateway.errors import ProviderError
from app.data.locations import CertTarget
from app.data.tourapi import TourApiClient, TourApiError
from app.main import create_app
from app.services.amenity_finder import AmenityFinder
from app.services.amenity_recommend import AmenityRecommendService

M = 1 / 111_195
PLACES = {
    1: CertTarget(1, "자갈치시장", 35.0966, 129.0306, None, 100, False),
    2: CertTarget(2, "범일동 철길", 35.1416, 129.0599, None, 100, False),
    3: CertTarget(3, "좌표없음", None, None, None, 100, False),
}
HEADERS = {"X-Notice-Version": NOTICE_VERSION}


@pytest.fixture(autouse=True)
def fake_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    async def cert_targets(_c: object, ids: list[int]) -> dict[int, CertTarget]:
        return {i: PLACES[i] for i in ids if i in PLACES}

    monkeypatch.setattr(location_repo, "cert_targets", cert_targets)


@asynccontextmanager
async def no_db() -> AsyncIterator[None]:
    yield None


def item(cid: str, lat: float, lon: float, lcls: str, title: str) -> dict:
    return {"contentid": cid, "title": title, "addr1": "부산 어딘가", "mapy": str(lat),
            "mapx": str(lon), "lclsSystm3": lcls}  # fmt: skip


class FakeTour:
    """부산 목록. 음식(39)은 1~2 구간 중간 지점 근처 카페·밥 두 곳.

    숙박(32)은 마지막 촬영지 근처 한 곳이고, lodging_fail이면 실패한다.
    """

    def __init__(self, lodging_fail: bool = False) -> None:
        self.lodging_fail = lodging_fail

    async def __call__(self, url: str, params: dict, *, timeout_s: float) -> dict:
        a, b = PLACES[1], PLACES[2]
        lat, lon = (a.lat + b.lat) / 2, (a.lon + b.lon) / 2  # type: ignore[operator]
        if params["contentTypeId"] == 32:
            if self.lodging_fail:
                raise TourApiError("timeout")
            h_lat, h_lon = b.lat + 200 * M, b.lon  # type: ignore[operator]
            items = [item("h1", h_lat, h_lon, "AC060200", "바다 게스트하우스")]
        else:
            items = [
                item("c1", lat + 100 * M, lon, "FD050100", "골목 카페"),
                item("r1", lat + 300 * M, lon, "FD010100", "국밥집"),
            ]
        return {"response": {"header": {"resultCode": "0000"}, "body": {"items": {"item": items}}}}


class FakeAI:
    """조건 구조화와 추천 이유를 작업 이름에 맞춰 답한다."""

    name = "ollama"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.tasks: list[str] = []

    async def complete(self, prompt, *, timeout_s: float) -> str:
        self.tasks.append(prompt.task)
        if self.fail:
            raise ProviderError("upstream", "꺼짐")
        if prompt.task == "structure_facility":
            return '{"kinds": ["food"], "food": ["카페"], "lodging": [], "keywords": []}'
        return (
            '{"reasons": [{"number": 1, "text": "골목 카페는 두 촬영지 사이에 있어요.",'
            ' "entities": ["골목 카페"]}]}'
        )


async def _no_sleep(_s: float) -> None:
    return None


def _app(ai: FakeAI, tour: FakeTour, log_dir: Path | None = None) -> FastAPI:
    app = create_app(Settings(database_url=None, log_dir=str(log_dir) if log_dir else None))
    client = ProviderChain({"ollama": ai}, DefaultPromptBuilder(), sleep=_no_sleep)
    finder = AmenityFinder(TourApiClient("k", get=tour))
    app.dependency_overrides[get_amenity_service] = lambda: AmenityRecommendService(
        client,
        finder,
        no_db,  # type: ignore[arg-type]
    )
    return app


def post(app: FastAPI, body: dict, headers: dict | None = None):
    return TestClient(app).post(
        "/api/courses/amenities", json=body, headers=HEADERS if headers is None else headers
    )


def test_recommends_with_ai_conditions_and_reasons() -> None:
    ai = FakeAI()
    res = post(_app(ai, FakeTour()), {"location_ids": [1, 2], "text": "카페 가고 싶어요"})

    body = res.json()
    assert res.status_code == 200
    assert body["conditions"]["status"] == "ai" and body["conditions"]["food"] == ["카페"]
    (seg,) = body["segments"]  # 음식만 원해서 숙박 구간은 빠짐
    assert (seg["key"], seg["from_order"], seg["to_order"]) == ("1-2", 1, 2)
    assert seg["anchor"] == "자갈치시장~범일동 철길 사이"
    assert seg["picks"][0]["name"] == "골목 카페" and seg["picks"][0]["ai_generated"] is True
    assert seg["picks"][0]["directions_url"].startswith("https://map.kakao.com/link/to/")
    assert [s["id"] for s in body["sources"]] == ["DATA-13"]
    assert set(ai.tasks) == {"structure_facility", "facility_reasons"}


def test_without_text_ai_is_not_called_for_conditions_and_lodging_failure_is_marked() -> None:
    ai = FakeAI(fail=True)
    res = post(_app(ai, FakeTour(lodging_fail=True)), {"location_ids": [1, 2]})

    body = res.json()
    assert body["conditions"]["status"] == "selected"
    food, lodging = body["segments"]
    assert [p["name"] for p in food["picks"]] == ["골목 카페", "국밥집"]
    assert all(p["ai_generated"] is False and p["reason"] for p in food["picks"])  # 템플릿 이유
    assert lodging["kind"] == "lodging" and lodging["picks"] == []
    assert lodging["notice"] == "시설 정보를 불러오지 못했어요"  # 0건과 구분(FR-418)
    assert "structure_facility" not in ai.tasks


def test_selected_overrides_and_ai_down_falls_back_to_manual() -> None:
    res = post(
        _app(FakeAI(fail=True), FakeTour()),
        {"location_ids": [1, 2], "text": "숙소만", "selected": {"kinds": ["lodging"]}},
    )

    body = res.json()
    assert body["conditions"]["status"] == "manual" and body["conditions"]["kinds"] == ["lodging"]
    assert [s["kind"] for s in body["segments"]] == ["lodging"]
    assert body["segments"][0]["picks"][0]["category"] == "게스트하우스·호스텔"
    assert any("직접 고를 수 있게" in n for n in body["notices"])


def test_condition_text_is_masked_in_request_log(tmp_path: Path) -> None:
    post(
        _app(FakeAI(), FakeTour(), tmp_path), {"location_ids": [1, 2], "text": "010-1234-5678 카페"}
    )

    log = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    assert "010-1234-5678" not in log and "[개인정보] 카페" in log


@pytest.mark.parametrize(
    ("body", "headers", "status", "code"),
    [
        ({"location_ids": [1, 2]}, {}, 403, "NOTICE_REQUIRED"),  # FR-701
        ({"location_ids": [1, 2]}, {"X-Notice-Version": "2025-01"}, 403, "NOTICE_REQUIRED"),
        ({"location_ids": [1, 999]}, None, 404, "LOCATION_NOT_FOUND"),
        ({"location_ids": [1, 3]}, None, 409, "LOCATION_NOT_LOCATED"),
        ({"location_ids": [1]}, None, 422, "INVALID_REQUEST"),
        ({"location_ids": [1, 2], "text": "가" * 201}, None, 422, "INVALID_REQUEST"),
        ({"location_ids": [1, 2], "selected": {"lodging": ["펜션"]}}, None, 422, "INVALID_REQUEST"),
    ],
)
def test_errors(body: dict, headers: dict | None, status: int, code: str) -> None:
    res = post(_app(FakeAI(), FakeTour()), body, headers)

    assert res.status_code == status and res.json()["error"]["code"] == code

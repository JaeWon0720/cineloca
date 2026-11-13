"""F2 API 형식 테스트 (API-005~007, FR-201~210, #27). 지금은 예시 응답으로 형식만 확인한다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app

HAEUNDAE = {"lat": 35.1587, "lon": 129.1604}


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)


def test_search_returns_hits_sorted_by_distance(client: TestClient) -> None:
    res = client.post("/api/locations/search", json={"base": HAEUNDAE})

    assert res.status_code == 200
    body = res.json()
    assert body["radius_m"] == 1000  # 기본 반경(FR-203)
    assert body["total"] == len(body["items"]) > 0
    distances = [h["distance_m"] for h in body["items"]]
    assert distances == sorted(distances)
    for hit in body["items"]:
        assert len(hit["works"]) == min(3, hit["work_count"])  # 최대 3편(FR-206)
        assert hit["directions_url"].startswith("https://map.kakao.com/link/to/")
    assert {s["id"] for s in body["sources"]} >= {"DATA-01"}  # 출처 카드(FR-704)


def test_search_does_not_echo_base_location(client: TestClient) -> None:
    res = client.post("/api/locations/search", json={"base": {"lat": 35.15871, "lon": 129.16042}})

    assert "35.15871" not in res.text and "129.16042" not in res.text  # NFR-003


def test_search_filters_and_empty_result(client: TestClient) -> None:
    far = client.post("/api/locations/search", json={"base": {"lat": 35.115, "lon": 129.041}})
    by_gu = client.post("/api/locations/search", json={"base": HAEUNDAE, "gu": ["사하구"]})
    by_year = client.post("/api/locations/search", json={"base": HAEUNDAE, "year_from": 2020})

    assert far.json()["total"] == 0  # 부산역 1km 안에는 예시 촬영지 없음 → 화면에서 반경 확대 안내
    assert by_gu.json()["total"] == 0
    assert [h["name"] for h in by_year.json()["items"]] == ["부산아쿠아리움"]


@pytest.mark.parametrize(
    "payload",
    [
        {"base": HAEUNDAE, "radius_m": 700},
        {"base": {"lat": 135, "lon": 129}},
        {"base": HAEUNDAE, "gu": ["강남구"]},
        {"base": HAEUNDAE, "year_from": 2020, "year_to": 2010},
        {"radius_m": 1000},
    ],
)
def test_search_rejects_invalid_request(client: TestClient, payload: dict) -> None:
    res = client.post("/api/locations/search", json=payload)

    assert res.status_code == 422
    error = res.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert "135" not in error["message"]  # 입력값을 되돌려 주지 않음


def test_location_detail(client: TestClient) -> None:
    body = client.get("/api/locations/1").json()

    assert body["located"] is True and body["lat"] and body["directions_url"]
    assert body["cert"] == {"eligible": True, "radius_m": 100, "excluded_reason": None}
    assert all("kmdb_matched" in w for w in body["works"])


def test_location_without_coordinates(client: TestClient) -> None:
    body = client.get("/api/locations/4").json()

    assert body["located"] is False
    assert body["lat"] is None and body["directions_url"] is None  # FR-210
    assert body["caution"] == "residential" and body["caution_notice"]  # FR-209
    assert body["cert"]["eligible"] is False  # FR-506


def test_location_not_found_uses_error_format(client: TestClient) -> None:
    res = client.get("/api/locations/999999")

    assert res.status_code == 404
    error = res.json()["error"]
    assert error == {
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
    assert body["radius_options"] == [500, 1000, 3000] and body["default_radius_m"] == 1000
    assert len(body["districts"]) == 16  # BR-15
    assert all(35.0 < d["lat"] < 35.4 and 128.8 < d["lon"] < 129.3 for d in body["districts"])
    assert body["stations"] == []


def test_openapi_lists_f2_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/locations/search" in paths and "post" in paths["/api/locations/search"]
    assert "/api/locations/{location_id}" in paths
    assert "/api/meta" in paths


def test_genre_filter_uses_groups(client: TestClient) -> None:
    meta = client.get("/api/meta").json()
    disaster = client.post("/api/locations/search", json={"base": HAEUNDAE, "genres": ["재난"]})
    raw_kmdb = client.post("/api/locations/search", json={"base": HAEUNDAE, "genres": ["코메디"]})

    assert meta["genres"][0] == "드라마" and len(meta["genres"]) == 12
    assert {h["name"] for h in disaster.json()["items"]} == {"해운대해수욕장", "해운대전통시장"}
    assert raw_kmdb.status_code == 422  # KMDb 원래 장르명이 아니라 묶음 이름으로 받는다

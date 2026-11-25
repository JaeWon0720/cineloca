"""검수 큐·촬영지 속성 테스트 (API-022·023, FR-609·610, BR-04·06·07).

DB 없이 판단 규칙과 API 형식을 본다.

DB 반영(태그 더하기, 처리 기록)은 test_admin_review_db.py가 본다.
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_admin_auth
from app.api.routers.admin_review import get_review_service
from app.core.config import Settings
from app.data.reviews import LocationAttrs, ReviewRow
from app.main import create_app
from app.services.admin_auth import AdminAuth, hash_password
from app.services.admin_review import ReviewError, check_location_patch, decide
from tests.test_admin_auth import PASSWORD

HASH = hash_password(PASSWORD, iterations=1_000)
T0 = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)
TAG = ReviewRow(
    id=6, kind="tag", target_type="location", target_id=5, target_name="온천천",
    target_year=None, target_exists=True, proposer="ai", score=None, status="pending",
    proposal={"tags": ["공원·산"], "reason": "하천 산책로"}, created_at=T0, handled_at=None,
)  # fmt: skip
RULE_MATCH = replace(
    TAG, id=1, kind="match", target_type="work", target_id=12, target_name="대외비",
    target_year=2023, proposer="rule", score=0.659,
    proposal={"kmdb_id": "K24589", "kmdb_title": "대외비", "prod_year": "2020"},
)  # fmt: skip
NOT_LISTED = {"verdict": "not_listed", "kmdb_id": None, "reason": "단편"}
AI_MATCH = replace(RULE_MATCH, id=4, proposer="ai", score=None, proposal=NOT_LISTED)


def code(e: pytest.ExceptionInfo[ReviewError]) -> tuple[int, str]:
    return e.value.status, e.value.code


# ── 처리 규칙(FR-609) ─────────────────────────────────────
def test_tag_approve_and_modify_add_tags() -> None:
    assert decide(TAG, "approve").add_tags == ["공원·산"]
    d = decide(TAG, "modify", tags=["해변", "해변", "공원·산"])
    assert (d.status, d.add_tags, d.proposal["tags"]) == (
        "modified",
        ["해변", "공원·산"],
        ["해변", "공원·산"],
    )
    assert d.proposal["reason"] == "하천 산책로"  # 제안 근거는 남긴다
    assert decide(TAG, "reject").add_tags == []


def test_tag_rules() -> None:
    failed = replace(TAG, proposal={"tags": [], "reason": None, "error": "AI_UNAVAILABLE"})
    with pytest.raises(ReviewError) as e:
        decide(failed, "approve")  # AI 실패 항목은 수정으로만 승인
    assert code(e) == (400, "INVALID_REQUEST")
    assert decide(failed, "modify", tags=["시장"]).add_tags == ["시장"]
    for bad in ([], ["야경"]):
        with pytest.raises(ReviewError):
            decide(TAG, "modify", tags=bad)


def test_match_decisions() -> None:
    assert decide(RULE_MATCH, "approve").proposal["kmdb_id"] == "K24589"
    d = decide(RULE_MATCH, "modify", kmdb_id="F11599")  # K 말고 A·F로 시작하는 번호도 있다
    assert (d.status, d.proposal["kmdb_id"], d.add_tags) == ("modified", "F11599", [])
    assert d.proposal["kmdb_title"] == "대외비"
    assert decide(AI_MATCH, "approve").status == "approved"  # 'KMDb에 없음'에 동의
    for bad in (None, "K1", "k24589", "K245890"):
        with pytest.raises(ReviewError):
            decide(RULE_MATCH, "modify", kmdb_id=bad)
    unsure = replace(AI_MATCH, proposal={"verdict": "unsure", "kmdb_id": None})
    with pytest.raises(ReviewError):
        decide(unsure, "approve")
    assert decide(unsure, "reject").status == "rejected"


def test_handled_or_gone_items() -> None:
    with pytest.raises(ReviewError) as e:
        decide(replace(TAG, status="approved"), "reject")
    assert code(e) == (409, "REVIEW_ALREADY_HANDLED")
    gone = replace(RULE_MATCH, target_exists=False, target_name=None)
    with pytest.raises(ReviewError) as e:
        decide(gone, "approve")
    assert code(e) == (409, "REVIEW_TARGET_GONE")
    assert decide(gone, "reject").status == "rejected"


def test_location_patch_ranges() -> None:
    assert check_location_patch({"cert_radius_m": 50, "safety_note": "  차도 주의 "}) == {
        "cert_radius_m": 50,
        "safety_note": "차도 주의",
    }
    assert check_location_patch({"safety_note": "  ", "caution": None}) == {
        "safety_note": None,
        "caution": None,
    }
    assert check_location_patch({"tags": []}) == {"tags": []}
    for bad in (
        {"cert_radius_m": 49},
        {"cert_radius_m": 301},
        {"cert_radius_m": None},
        {"cert_excluded": None},
        {"tags": None},
        {"tags": ["야경"]},
        {"safety_note": "가" * 201},
    ):
        with pytest.raises(ReviewError):
            check_location_patch(bad)


# ── API ──────────────────────────────────────────────────
ATTRS = LocationAttrs(
    5, "온천천", "동래구", "부산 동래구", True, ["공원·산"], None, None, 100, False
)


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def list(self, status, kind, page, size):
        self.calls.append(("list", (status, kind, page, size)))
        gone = replace(RULE_MATCH, id=9, target_exists=False, target_name=None)
        return {"total": 3, "page": page, "size": size, "counts": {"pending": 3, "handled": 0},
                "items": [TAG, AI_MATCH, gone]}  # fmt: skip

    async def handle(self, review_id, action, tags=None, kmdb_id=None):
        self.calls.append(("handle", (review_id, action, tags, kmdb_id)))
        if review_id == 404:
            raise ReviewError(404, "REVIEW_NOT_FOUND", "검수 항목을 찾지 못했습니다.")
        return replace(TAG, status="approved", handled_at=T0)

    async def location(self, location_id):
        if location_id != 5:
            raise ReviewError(404, "LOCATION_NOT_FOUND", "촬영지를 찾을 수 없습니다.")
        return ATTRS

    async def update_location(self, location_id, values):
        self.calls.append(("update", values))
        return replace(ATTRS, **check_location_patch(values))


def client(service: FakeService, login: bool = True) -> TestClient:
    app = create_app(Settings(database_url=None))
    auth = AdminAuth(HASH)  # 세션을 기억하도록 요청마다 같은 인증기를 쓴다
    app.dependency_overrides[get_admin_auth] = lambda: auth
    app.dependency_overrides[get_review_service] = lambda: service
    c = TestClient(app, base_url="https://testserver")
    if login:
        assert c.post("/api/admin/session", json={"password": PASSWORD}).status_code == 200
    return c


def test_admin_login_required() -> None:
    service = FakeService()
    c = client(service, login=False)
    paths = [
        ("GET", "/api/admin/reviews"),
        ("PATCH", "/api/admin/reviews/1"),
        ("GET", "/api/admin/locations/5"),
        ("PATCH", "/api/admin/locations/5"),
    ]
    for method, path in paths:
        res = c.request(method, path, json={"action": "approve"})
        assert res.status_code == 401 and res.json()["error"]["code"] == "ADMIN_REQUIRED"
    assert service.calls == []


def test_list_reviews_shape() -> None:
    service = FakeService()
    body = client(service).get("/api/admin/reviews?status=handled&kind=match&size=5").json()

    assert service.calls == [("list", ("handled", "match", 1, 5))]
    assert body["counts"] == {"pending": 3, "handled": 0} and body["total"] == 3
    assert set(body["items"][0]) == {
        "id", "kind", "target_type", "target_id", "target_name", "target_year", "proposer",
        "score", "status", "proposal", "created_at", "handled_at",
    }  # fmt: skip
    assert body["items"][1]["proposal"]["verdict"] == "not_listed"
    assert body["items"][2]["target_name"] == "(삭제됨)"
    c = client(service)
    assert c.get("/api/admin/reviews?size=51").status_code == 422
    assert c.get("/api/admin/reviews?status=sql").status_code == 422


def test_handle_review_passes_values_and_maps_errors() -> None:
    service = FakeService()
    c = client(service)
    res = c.patch("/api/admin/reviews/6", json={"action": "modify", "tags": ["해변"]})

    assert res.status_code == 200 and res.json()["status"] == "approved"
    assert service.calls[-1] == ("handle", (6, "modify", ["해변"], None))
    res = c.patch("/api/admin/reviews/404", json={"action": "reject"})
    assert res.status_code == 404 and res.json()["error"]["code"] == "REVIEW_NOT_FOUND"
    assert c.patch("/api/admin/reviews/6", json={"action": "delete"}).status_code == 422


def test_location_get_and_patch_only_sent_fields() -> None:
    service = FakeService()
    c = client(service)
    assert c.get("/api/admin/locations/5").json()["cert_radius_m"] == 100
    assert c.get("/api/admin/locations/6").json()["error"]["code"] == "LOCATION_NOT_FOUND"

    res = c.patch("/api/admin/locations/5", json={"cert_radius_m": 150, "caution": None})
    assert res.status_code == 200
    assert service.calls[-1] == ("update", {"cert_radius_m": 150, "caution": None})
    assert res.json()["cert_radius_m"] == 150
    res = c.patch("/api/admin/locations/5", json={"cert_radius_m": 400})
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_REQUEST"
    assert c.patch("/api/admin/locations/5", json={"caution": "home"}).status_code == 422

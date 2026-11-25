"""추천 코스 관리 테스트 (API-024, FR-614). DB 없이 후보 고르기 규칙과 API 형식을 본다."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_admin_auth
from app.api.routers.admin_courses import get_course_admin
from app.core.config import Settings
from app.data.courses import ThemePlace
from app.main import create_app
from app.services.admin_auth import AdminAuth, hash_password
from app.services.course_admin import CourseAdminError, StopEdit, check_stops, pick_cluster
from tests.test_admin_auth import PASSWORD

HASH = hash_password(PASSWORD, iterations=1_000)


def test_pick_cluster_starts_from_most_filmed_place() -> None:
    places = [
        ThemePlace(9, 35.10, 129.00, 3),  # 작품이 가장 많은 곳이 기준
        ThemePlace(1, 35.30, 129.30, 2),
        ThemePlace(2, 35.101, 129.00, 1),
        ThemePlace(3, 35.11, 129.00, 1),
    ]
    assert [p.id for p in pick_cluster(places, 3)] == [9, 2, 3]
    assert pick_cluster([], 3) == []


def test_check_stops() -> None:
    check_stops([StopEdit(1), StopEdit(2, 0)])
    for bad in ([StopEdit(1)], [StopEdit(1), StopEdit(1)], [StopEdit(i) for i in range(11)],
                [StopEdit(1), StopEdit(2, 241)]):  # fmt: skip
        with pytest.raises(CourseAdminError):
            check_stops(bad)


DETAIL = {
    "id": 3, "title": "「해운대」 촬영지 코스", "theme": "work", "theme_ref": "1",
    "status": "draft", "approved_at": None, "start_station_id": None,
    "start": {"type": "location", "label": "서쪽", "lat": 35.16, "lon": 129.15},
    "stops": [], "legs": [], "travel_min": 0, "stay_min": 0, "total_min": 0,
    "total_distance_m": 0, "optimized": False, "subway_data": True,
}  # fmt: skip


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def list(self, status, theme):
        self.calls.append(("list", (status, theme)))
        return {"items": []}

    async def create_candidate(self, theme, ref, max_stops):
        self.calls.append(("create", (theme, ref, max_stops)))
        if ref == "99":
            raise CourseAdminError(404, "THEME_NOT_FOUND", "작품·인물을 찾을 수 없습니다.")
        return DETAIL

    async def get(self, cid):
        return DETAIL

    async def update(self, cid, **kw):
        self.calls.append(("update", kw))
        return DETAIL

    async def set_status(self, cid, approved):
        self.calls.append(("status", approved))
        return {**DETAIL, "status": "approved" if approved else "draft"}

    async def delete_draft(self, cid):
        self.calls.append(("delete", cid))


def client(fake: Fake, login: bool = True) -> TestClient:
    app = create_app(Settings(database_url=None))
    auth = AdminAuth(HASH)
    app.dependency_overrides[get_admin_auth] = lambda: auth
    app.dependency_overrides[get_course_admin] = lambda: fake
    c = TestClient(app, base_url="https://testserver")
    if login:
        assert c.post("/api/admin/session", json={"password": PASSWORD}).status_code == 200
    return c


def test_admin_login_required() -> None:
    fake = Fake()
    c = client(fake, login=False)
    assert c.get("/api/admin/courses").json()["error"]["code"] == "ADMIN_REQUIRED"
    assert c.post("/api/admin/courses", json={"theme": "work", "theme_ref": "1"}).status_code == 401
    assert c.delete("/api/admin/courses/3").status_code == 401
    assert fake.calls == []


def test_create_list_and_errors() -> None:
    fake = Fake()
    c = client(fake)
    res = c.post("/api/admin/courses", json={"theme": "district", "theme_ref": " 해운대구 "})
    assert res.status_code == 201 and res.json()["status"] == "draft"
    assert fake.calls[-1] == ("create", ("district", "해운대구", 5))
    res = c.post("/api/admin/courses", json={"theme": "work", "theme_ref": "99"})
    assert res.status_code == 404 and res.json()["error"]["code"] == "THEME_NOT_FOUND"
    bad = {"theme": "work", "theme_ref": "1", "max_stops": 11}
    assert c.post("/api/admin/courses", json=bad).status_code == 422
    assert c.get("/api/admin/courses?status=approved&theme=person").json() == {"items": []}
    assert fake.calls[-1] == ("list", ("approved", "person"))


def test_patch_edits_then_status_in_one_request() -> None:
    fake = Fake()
    c = client(fake)
    body = {"title": "새 제목", "stops": [{"location_id": 3, "stay_min": 30}, {"location_id": 1}],
            "status": "approved"}  # fmt: skip
    res = c.patch("/api/admin/courses/3", json=body)

    assert res.status_code == 200 and res.json()["status"] == "approved"
    kind, kw = fake.calls[0]
    assert kind == "update" and kw["title"] == "새 제목" and kw["clear_start"] is False
    assert kw["stops"] == [StopEdit(3, 30), StopEdit(1, 20)]
    assert fake.calls[1] == ("status", True)

    fake.calls.clear()
    c.patch("/api/admin/courses/3", json={"start_station_id": None})
    assert fake.calls[0][1]["clear_start"] is True
    fake.calls.clear()
    c.patch("/api/admin/courses/3", json={"status": "draft"})
    assert fake.calls == [("status", False)]  # 상태만 바꾸면 수정은 부르지 않는다
    assert c.delete("/api/admin/courses/3").status_code == 204

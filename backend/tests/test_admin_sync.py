"""동기화 요청·리포트 테스트 (API-021, FR-612·613·616, NFR-005·018). DB 대신 가짜 서비스를 쓴다."""

from collections.abc import Iterator
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_admin_auth
from app.api.routers.admin import get_sync_service
from app.batch.report import SyncCounts, build_report, failure
from app.core.config import Settings
from app.main import create_app
from app.services.admin_auth import AdminAuth, hash_password
from app.services.admin_sync import (
    ActiveRun,
    Schedule,
    SyncOverview,
    SyncRequest,
    alerts_for,
    usage_items,
)
from tests.test_admin_auth import PASSWORD

HASH = hash_password(PASSWORD, iterations=1_000)
T0 = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)


def report(run_id: int, status: str, reasons: list[str] | None = None):
    return build_report(
        run_id=run_id,
        trigger="manual",
        status=status,  # type: ignore[arg-type]
        started_at=T0,
        finished_at=T0,
        counts=SyncCounts(works=40, locations=100),
        prev_counts=SyncCounts(works=80, locations=200),
        failures=[failure("count_guard", r) for r in reasons or []],
    )


# ── 호출량·경고 ─────────────────────────────────────────
def test_usage_levels_and_limits() -> None:
    items = usage_items({"gemini": 14, "DATA-01": 1000, "kakao": 3}, gemini_limit=20)

    by = {u.key: u for u in items}
    assert (by["gemini"].limit, by["gemini"].ratio, by["gemini"].level) == (20, 0.7, "warning")
    assert by["DATA-01"].level == "exhausted"  # FR-601 하루 1,000건
    assert (by["kakao"].limit, by["kakao"].level) == (None, "ok")
    assert [u.key for u in items] == ["DATA-01", "gemini", "kakao"]


def test_alerts_for_guard_failure_and_quota() -> None:
    guarded = report(3, "guarded", ["작품 수가 직전보다 50% 줄었습니다"])
    usage = usage_items({"gemini": 20}, gemini_limit=20)

    alerts = alerts_for([guarded], usage)

    assert "건수 가드" in alerts[0] and "한도(20회)를 모두" in alerts[1]
    assert alerts_for([report(4, "succeeded")], []) == []
    assert "실패" in alerts_for([report(5, "failed")], [])[0]


# ── API ──────────────────────────────────────────────
class FakeSync:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.requests = 0

    async def request(self) -> SyncRequest:
        self.requests += 1
        return SyncRequest(7, not self.active)

    async def overview(self) -> SyncOverview:
        reports = [report(6, "guarded", ["촬영지 수가 최소 기준(150곳)에 못 미칩니다"])]
        usage = usage_items({"gemini": 15}, gemini_limit=20)
        return SyncOverview(
            active=ActiveRun(7, "manual", "requested", T0, None) if self.active else None,
            reports=reports,
            usage=usage,
            usage_day=date(2026, 11, 25),
            schedule=Schedule(7, T0, None),
            alerts=alerts_for(reports, usage),
        )


def _client(sync: FakeSync, login: bool = True) -> TestClient:
    app = create_app(Settings(database_url=None))
    app.dependency_overrides[get_admin_auth] = lambda: AUTH
    app.dependency_overrides[get_sync_service] = lambda: sync
    client = TestClient(app, base_url="https://testserver")
    if login:
        assert client.post("/api/admin/session", json={"password": PASSWORD}).status_code == 200
    return client


AUTH = AdminAuth(HASH)


@pytest.fixture
def sync() -> Iterator[FakeSync]:
    yield FakeSync()


def test_sync_requires_admin(sync: FakeSync) -> None:
    client = _client(sync, login=False)

    assert client.get("/api/admin/sync").json()["error"]["code"] == "ADMIN_REQUIRED"
    assert client.post("/api/admin/sync").status_code == 401
    assert sync.requests == 0


def test_request_sync_202_then_200_when_already_active() -> None:
    first = _client(FakeSync(active=False)).post("/api/admin/sync")
    again = _client(FakeSync(active=True)).post("/api/admin/sync")

    assert first.status_code == 202 and first.json()["created"] is True
    assert again.status_code == 200 and again.json() == {
        "run_id": 7,
        "created": False,
        "message": "이미 대기·실행 중인 동기화가 있습니다.",
    }


def test_overview_has_reports_guard_usage_schedule() -> None:
    body = _client(FakeSync(active=True)).get("/api/admin/sync").json()

    assert body["active"]["status"] == "requested" and body["active"]["run_id"] == 7
    assert body["reports"][0]["status"] == "guarded"
    assert body["reports"][0]["guard"] == ["촬영지 수가 최소 기준(150곳)에 못 미칩니다"]
    assert body["reports"][0]["rates"] is not None  # FR-613 비율
    assert body["usage"] == [
        {"key": "gemini", "count": 15, "limit": 20, "ratio": 0.75, "level": "warning"}
    ]
    assert body["usage_day"] == "2026-11-25"
    assert body["schedule"]["interval_days"] == 7
    assert any("건수 가드" in a for a in body["alerts"])

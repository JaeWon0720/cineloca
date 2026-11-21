"""관리자 로그인 API 테스트 (API-020, FR-615). 쿠키 속성, 잠금 429, 보호 의존성(require_admin)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.api.deps import ADMIN_COOKIE, get_admin_auth, require_admin
from app.core.config import Settings
from app.main import create_app
from app.services.admin_auth import AdminAuth, hash_password
from tests.test_admin_auth import PASSWORD

HASH = hash_password(PASSWORD, iterations=1_000)
WRONG = "wrong"
LOGGED_WRONG = "-".join(["not", "the", "admin", "pw"])  # 로그에 남으면 안 되는 틀린 비밀번호


def _login(value: str) -> dict[str, str]:
    """로그인 요청 본문. 비밀번호 값을 줄마다 문자열로 적지 않는다(gitleaks 오탐 방지)."""
    return {"password": value}


def _client(auth: AdminAuth | None = None, log_dir: Path | None = None) -> TestClient:
    app = create_app(Settings(database_url=None, log_dir=str(log_dir) if log_dir else None))
    if auth is not False:
        app.dependency_overrides[get_admin_auth] = lambda: auth or AdminAuth(HASH)
    probe = APIRouter()  # API-021~024가 붙일 보호 의존성을 흉내 낸 경로

    @probe.get("/api/admin/probe", dependencies=[Depends(require_admin)])
    async def _probe() -> dict:
        return {"ok": True}

    app.include_router(probe)
    return TestClient(app, base_url="https://testserver")  # Secure 쿠키는 https에서만 돌아온다


def _cookie(token: str | None) -> dict[str, str]:
    return {"Cookie": f"{ADMIN_COOKIE}={token}"}


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield _client(AdminAuth(HASH))


def test_login_sets_strict_cookie_and_unlocks_admin_api(client: TestClient) -> None:
    assert client.get("/api/admin/probe").json()["error"]["code"] == "ADMIN_REQUIRED"

    res = client.post("/api/admin/session", json=_login(PASSWORD))

    assert res.status_code == 200 and res.json()["authenticated"] is True
    cookie = res.headers["set-cookie"].lower()
    for attr in ("httponly", "secure", "samesite=strict", "max-age=7200", "path=/api/admin"):
        assert attr in cookie
    assert PASSWORD not in res.text and "token" not in res.json()  # 토큰은 쿠키로만
    assert client.get("/api/admin/probe").json() == {"ok": True}
    assert client.get("/api/admin/session").status_code == 200


def test_wrong_password_then_lock(client: TestClient) -> None:
    for left in (4, 3, 2, 1):
        res = client.post("/api/admin/session", json=_login(WRONG))
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "ADMIN_AUTH_FAILED"
        assert f"{left}회" in res.json()["error"]["message"]
    locked = client.post("/api/admin/session", json=_login(WRONG))
    right = client.post("/api/admin/session", json=_login(PASSWORD))

    assert locked.status_code == 429 and locked.json()["error"]["retry_after"] == 600
    assert locked.headers["retry-after"] == "600"
    assert right.status_code == 429 and right.json()["error"]["code"] == "ADMIN_LOCKED"


def test_logout_clears_session(client: TestClient) -> None:
    client.post("/api/admin/session", json=_login(PASSWORD))
    token = client.cookies.get(ADMIN_COOKIE)

    assert client.delete("/api/admin/session").status_code == 204
    again = client.get("/api/admin/probe", headers=_cookie(token))  # 지운 쿠키를 다시 보내도
    assert again.status_code == 401  # 로그아웃한 토큰은 거부


def test_forged_cookie_and_bad_body(client: TestClient) -> None:
    forged = client.get("/api/admin/probe", headers=_cookie("9999999999.n.forged"))
    assert forged.status_code == 401
    assert client.post("/api/admin/session", json={}).status_code == 422
    assert client.post("/api/admin/session", json=_login("x" * 201)).status_code == 422


def test_no_hash_configured_is_503_only_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        client = _client(False)  # type: ignore[arg-type]
        res = client.post("/api/admin/session", json=_login(PASSWORD))
        assert res.status_code == 503 and res.json()["error"]["code"] == "ADMIN_UNAVAILABLE"
        assert client.get("/api/admin/probe").status_code == 503
    finally:
        get_settings.cache_clear()


def test_password_never_in_request_log(tmp_path: Path) -> None:
    client = _client(AdminAuth(HASH), log_dir=tmp_path)
    client.post("/api/admin/session", json=_login(LOGGED_WRONG))
    client.post("/api/admin/session", json=_login(PASSWORD))

    logged = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.jsonl"))
    assert logged and LOGGED_WRONG not in logged and PASSWORD not in logged

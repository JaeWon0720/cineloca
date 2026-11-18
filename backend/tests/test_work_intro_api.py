"""F1 AI 작품 소개 API 테스트 (API-004, FR-108~111). DB·AI 없이 가짜 조회와 가짜 공급자로 본다."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routers.work_intro import get_intro_service, get_work_facts
from app.core.notice import NOTICE_VERSION
from app.data.ai_gateway import DefaultPromptBuilder, ProviderChain, ProviderError
from app.data.ai_gateway.testing import FakeProvider
from app.main import create_app
from app.services.work_intro import LocationFact, WorkFacts, WorkIntroService

WORK = WorkFacts(
    work_id=31,
    title="친구",
    kmdb_matched=True,
    release_year=2001,
    director="곽경택",
    synopsis="1976년 부산, 네 친구가 함께 자란다. 결말에서 동수는 죽음을 맞는다.",
    locations=(LocationFact("범일동 철길", "동구"), LocationFact("자갈치시장", "중구")),
)
UNMATCHED = WorkFacts(work_id=40, title="광고 A", kmdb_matched=False)
GOOD = {
    "sentences": [
        {"text": "친구는 곽경택 감독의 2001년 작품입니다.", "entities": ["친구", "곽경택"]},
        {"text": "동구 범일동 철길이 촬영지입니다.", "entities": ["동구", "범일동 철길"]},
        {"text": "자갈치시장은 최고의 명소입니다.", "entities": ["자갈치시장"]},
    ]
}


class FakeFacts:
    async def get(self, work_id: int) -> WorkFacts | None:
        return {31: WORK, 40: UNMATCHED}.get(work_id)


async def _no_sleep(_s: float) -> None:
    return None


def make_client(*replies) -> tuple[TestClient, FakeProvider]:
    gemini = FakeProvider("gemini", list(replies))
    chain = ProviderChain({"gemini": gemini}, DefaultPromptBuilder(), sleep=_no_sleep)
    app = create_app()
    app.dependency_overrides[get_intro_service] = lambda: WorkIntroService(chain)
    app.dependency_overrides[get_work_facts] = lambda: FakeFacts()
    return TestClient(app, headers={"X-Notice-Version": NOTICE_VERSION}), gemini  # FR-701


@pytest.fixture
def ok_client() -> Iterator[tuple[TestClient, FakeProvider]]:
    yield make_client(GOOD)


def test_intro_ok_with_ai_label(ok_client) -> None:
    client, gemini = ok_client

    res = client.post("/api/works/31/intro")

    body = res.json()
    assert res.status_code == 200
    assert body["status"] == "ok" and body["spoiler"] == "none"
    assert body["ai_generated"] is True and len(body["sentences"]) >= 2  # FR-707, FR-110
    assert body["fallback"] is None and body["notice"] is None
    assert "줄거리" not in gemini.calls[0].user  # '없음' 단계는 줄거리를 보내지 않음


def test_partial_level_sends_synopsis(ok_client) -> None:
    client, gemini = ok_client

    body = client.post("/api/works/31/intro", json={"spoiler": "partial"}).json()

    assert body["spoiler"] == "partial"
    assert "줄거리: 1976년 부산" in gemini.calls[0].user


def test_ai_failure_degrades_to_original() -> None:  # FR-110, AC-04
    client, _ = make_client(ProviderError("timeout"), ProviderError("timeout"))

    res = client.post("/api/works/31/intro")

    body = res.json()
    assert res.status_code == 200
    assert body["status"] == "degraded" and body["ai_generated"] is False
    assert body["fallback"]["synopsis"].startswith("1976년 부산")
    assert body["fallback"]["locations"][0] == {"name": "범일동 철길", "gu": "동구"}
    assert body["notice"] and body["reason"]


def test_unmatched_work_is_not_eligible() -> None:  # FR-108
    client, gemini = make_client()

    body = client.post("/api/works/40/intro").json()

    assert body["status"] == "not_eligible" and body["sentences"] == []
    assert "KMDb" in body["notice"] and gemini.calls == []


def test_unknown_work_and_bad_level() -> None:
    client, _ = make_client()

    missing = client.post("/api/works/999/intro")
    bad = client.post("/api/works/31/intro", json={"spoiler": "all"})

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "WORK_NOT_FOUND"
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_REQUEST"


def test_intro_requires_notice_version() -> None:
    client, _ = make_client()
    res = TestClient(client.app).post("/api/works/31/intro")  # X-Notice-Version 없음

    assert res.status_code == 403 and res.json()["error"]["code"] == "NOTICE_REQUIRED"

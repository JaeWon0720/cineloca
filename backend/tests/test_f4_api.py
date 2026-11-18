"""F4 API 계층 테스트 (API-013·014·015, FR-401~412). 실제 AI·DB 없이 형식·오류·흐름만 본다.

AI는 가짜 공급자(FakeProvider), 사전 대조·검색은 test_query_service의 가짜 포트를 쓴다.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.api.routers.query import get_filter_search, get_query_service, to_filters
from app.api.schemas.query import FiltersModel
from app.data.ai_gateway import ProviderError
from app.data.db import DatabaseUnavailable
from app.main import create_app
from app.services.filter_search import FilterSearchService
from app.services.query_service import AnchorFilter, Filters, QueryService, Ref, TravelFilter
from tests.test_query_service import SUMMARY, FakeSearcher, cond, make

HERE = {"lat": 35.123456, "lon": 129.123456}


@dataclass
class Rig:
    client: TestClient
    service: QueryService
    searcher: FakeSearcher
    provider: object

    def ask(self, text: str, session: str | None = None, **body: object):
        headers = {"X-Session-Id": session} if session else {}
        return self.client.post("/api/query", json={"text": text, **body}, headers=headers)


def rig(*replies: object, searcher: FakeSearcher | None = None) -> Rig:
    service, provider, _, searcher = make(*replies, searcher=searcher)
    app = create_app()
    app.dependency_overrides[get_query_service] = lambda: service
    app.dependency_overrides[get_filter_search] = lambda: FilterSearchService(searcher)
    return Rig(TestClient(app), service, searcher, provider)


@pytest.fixture
def plain() -> Iterator[Rig]:
    yield rig(cond(work_titles=["친구"]), SUMMARY)


def test_ask_returns_query_result_fields(plain: Rig) -> None:
    res = plain.ask("친구 촬영지 알려줘")

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "status", "session_id", "notices", "masked", "chips", "unknown", "relaxed", "total",
        "hits", "summary", "ai_generated", "choices", "filter_panel",
    }  # fmt: skip
    assert body["status"] == "ok" and body["session_id"]
    assert body["chips"] == [{"key": "works", "label": "작품: 친구"}]
    assert body["total"] == 2
    assert body["hits"][0] == {
        "location_id": 1,
        "name": "자갈치시장",
        "gu": "중구",
        "lat": 35.09,
        "lon": 129.03,
        "works": [{"title": "친구", "year": 2001}],
        "station": "자갈치역",
        "walk_min": 5,
        "source_ids": [],
    }
    assert len(body["summary"]) == 2 and body["ai_generated"] is True  # FR-707
    assert body["choices"] == [] and body["filter_panel"] is None


def test_base_point_comes_from_body_only_and_is_not_echoed() -> None:
    r = rig(cond(anchor={"type": "current"}, travel={"mode": "walk", "max_min": 10}), SUMMARY)
    res = r.ask("내 주변 걸어서 10분", base=HERE)

    assert res.status_code == 200
    assert r.searcher.calls[-1][1] == (35.123456, 129.123456)
    assert "35.123456" not in res.text and "129.123456" not in res.text  # BR-10
    assert {"key": "anchor", "label": "기준: 내 위치"} in res.json()["chips"]


def test_follow_up_uses_session_header() -> None:
    r = rig(cond(work_titles=["친구"]), SUMMARY, cond(districts=["중구"]), SUMMARY)
    first = r.ask("친구 촬영지").json()
    second = r.ask("그중 중구만", session=first["session_id"]).json()

    assert second["session_id"] == first["session_id"]
    assert [c["key"] for c in second["chips"]] == ["works", "districts"]  # FR-408


@pytest.mark.parametrize("text", ["", "   ", "가" * 201])
def test_bad_text_is_400_without_calling_ai(plain: Rig, text: str) -> None:
    res = plain.ask(text)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_QUERY"
    assert plain.provider.calls == []  # type: ignore[attr-defined]


def test_malformed_request_is_422_without_echoing_values(plain: Rig) -> None:
    assert plain.client.post("/api/query", json={}).status_code == 422
    res = plain.ask("친구 촬영지", base={"lat": 135.123456, "lon": 129.0})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_REQUEST" and "135.123456" not in res.text


def test_choose_then_search() -> None:
    r = rig(cond(anchor={"type": "station", "name": "서면"}), SUMMARY)
    asked = r.ask("서면역 근처 촬영지").json()

    assert asked["status"] == "choose" and asked["hits"] == []
    assert asked["choices"] == [
        {
            "key": "anchor",
            "name": "서면",
            "options": [
                {"id": "s1", "label": "서면역(1호선)"},
                {"id": "s2", "label": "서면역(2호선)"},
            ],
        }
    ]
    session = {"X-Session-Id": asked["session_id"]}
    res = r.client.post(
        "/api/query/choose", json={"key": "anchor", "option_id": "s2"}, headers=session
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok" and body["total"] == 2
    assert body["chips"] == [{"key": "anchor", "label": "기준: 서면역(2호선)"}]
    assert r.searcher.calls[-1][0].anchor == AnchorFilter("station", Ref("s2", "서면역(2호선)"))


def test_choose_without_pending_choice_is_400(plain: Rig) -> None:
    body = {"key": "anchor", "option_id": "s1"}
    res = plain.client.post("/api/query/choose", json=body)  # 세션 없음
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_QUERY"
    session = {"X-Session-Id": plain.ask("친구 촬영지").json()["session_id"]}
    assert plain.client.post("/api/query/choose", json=body, headers=session).status_code == 400
    bad_key = {"key": "genres", "option_id": "s1"}
    assert plain.client.post("/api/query/choose", json=bad_key).status_code == 422


def test_remove_chip_searches_again() -> None:
    r = rig(cond(work_titles=["친구"], districts=["중구"]), SUMMARY, SUMMARY)
    session = {"X-Session-Id": r.ask("중구의 친구 촬영지").json()["session_id"]}
    res = r.client.post("/api/query/chips/remove", json={"key": "districts"}, headers=session)

    assert res.status_code == 200
    assert [c["key"] for c in res.json()["chips"]] == ["works"]
    assert r.searcher.calls[-1][0].districts == ()
    again = r.client.post("/api/query/chips/remove", json={"key": "districts"}, headers=session)
    assert again.status_code == 400  # 이미 없는 조건


def test_reset_session_forgets_context() -> None:
    r = rig(cond(work_titles=["친구"]), SUMMARY, cond(districts=["중구"]), SUMMARY)
    first = r.ask("친구 촬영지").json()
    res = r.client.delete("/api/query/session", headers={"X-Session-Id": first["session_id"]})

    assert res.status_code == 204 and res.content == b""
    second = r.ask("그중 중구만", session=first["session_id"]).json()
    assert second["session_id"] != first["session_id"]
    assert [c["key"] for c in second["chips"]] == ["districts"]
    assert r.client.delete("/api/query/session").status_code == 204  # 세션이 없어도 204


def test_ai_down_returns_200_with_filter_panel() -> None:
    r = rig(ProviderError("upstream", "down"), ProviderError("upstream", "down"))
    res = r.ask("친구 촬영지")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ai_unavailable" and body["hits"] == []
    assert body["filter_panel"] == FiltersModel().model_dump()  # FR-411
    assert r.searcher.calls == []


def test_sixth_ai_request_in_a_minute_is_429() -> None:
    r = rig(*[cond(out_of_scope=True)] * 5)
    session = r.ask("날씨 어때").json()["session_id"]
    for _ in range(4):
        assert r.ask("날씨 어때", session=session).json()["status"] == "out_of_scope"
    res = r.ask("날씨 어때", session=session)

    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"  # ERR-011
    assert int(res.headers["Retry-After"]) >= 1


def test_database_down_is_503() -> None:
    class DownSearcher(FakeSearcher):
        async def search(self, filters, *, base_point, limit):
            raise DatabaseUnavailable

    r = rig(cond(work_titles=["친구"]), searcher=DownSearcher())
    res = r.ask("친구 촬영지")
    assert res.status_code == 503 and res.json()["error"]["code"] == "DB_UNAVAILABLE"
    res = r.client.post("/api/search", json={"conditions": {}})
    assert res.status_code == 503


# ── API-014 필터 패널 검색 ─────────────────────────────────
CONDITIONS = {
    "works": [{"id": "3", "name": "해운대"}],
    "genres": ["재난"],
    "districts": ["해운대구"],
    "year_from": 2000,
    "year_to": 2010,
    "anchor": {"type": "station", "ref": {"id": "203", "name": "해운대역"}},
    "travel": {"mode": "walk", "max_min": 10},
}


def test_filter_search_passes_conditions_without_ai(plain: Rig) -> None:
    res = plain.client.post("/api/search", json={"conditions": CONDITIONS, "base": HERE})

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "status", "notices", "chips", "relaxed", "total", "hits", "filter_panel"
    }  # fmt: skip
    assert body["status"] == "ok" and body["total"] == 2 and len(body["hits"]) == 2
    assert [c["key"] for c in body["chips"]] == [
        "works", "genres", "districts", "years", "anchor", "travel"
    ]  # fmt: skip
    filters, base_point = plain.searcher.calls[-1]
    assert base_point == (35.123456, 129.123456) and "35.123456" not in res.text
    assert filters == Filters(
        works=(Ref("3", "해운대"),),
        genres=("재난",),
        districts=("해운대구",),
        year_from=2000,
        year_to=2010,
        anchor=AnchorFilter("station", Ref("203", "해운대역")),
        travel=TravelFilter("walk", 10),
    )
    assert plain.provider.calls == []  # type: ignore[attr-defined]


def test_filter_search_relaxes_conditions_in_order() -> None:
    r = rig(searcher=FakeSearcher(empty_while=("travel", "years")))
    body = r.client.post("/api/search", json={"conditions": CONDITIONS}).json()

    assert body["status"] == "ok" and body["relaxed"] == ["이동 시간", "개봉연도"]  # FR-405
    assert "years" not in [c["key"] for c in body["chips"]]
    r = rig(searcher=FakeSearcher(empty_while=("works",)))
    body = r.client.post("/api/search", json={"conditions": CONDITIONS}).json()
    assert body["status"] == "not_found" and body["hits"] == []
    assert body["relaxed"] == ["이동 시간", "개봉연도", "장르", "구·군"]
    assert body["filter_panel"] == FiltersModel.model_validate(CONDITIONS).model_dump()


def test_filter_search_drops_current_anchor_without_base(plain: Rig) -> None:
    conditions = {"anchor": {"type": "current"}, "travel": {"mode": "walk", "max_min": 10}}
    body = plain.client.post("/api/search", json={"conditions": conditions}).json()

    assert body["chips"] == [] and len(body["notices"]) == 1
    assert plain.searcher.calls[-1][0] == Filters()


@pytest.mark.parametrize(
    "conditions",
    [
        {"genres": ["없는장르"]},
        {"districts": ["서울"]},
        {"year_from": 2020, "year_to": 2010},
        {"travel": {"mode": "walk", "max_min": 0}},
        {"works": [{"id": "", "name": "x"}]},
        {"result_type": "sql"},
    ],
)
def test_filter_search_rejects_values_outside_allowed_lists(plain: Rig, conditions: dict) -> None:
    res = plain.client.post("/api/search", json={"conditions": conditions})
    assert res.status_code == 422 and plain.searcher.calls == []


def test_to_filters_keeps_any_station_and_result_type() -> None:
    m = FiltersModel.model_validate({"anchor": {"type": "station"}, "result_type": "work"})
    assert to_filters(m) == Filters(anchor=AnchorFilter("station"), result_type="work")


def test_default_service_builds_without_ai_keys_or_network(monkeypatch) -> None:
    for name in ("GEMINI_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "ollama")  # 주소 없는 잘못된 설정 → AI 없이 시작
    assert isinstance(get_query_service.__wrapped__(), QueryService)
    monkeypatch.setenv("AI_PROVIDER", "none")
    assert isinstance(get_query_service.__wrapped__(), QueryService)

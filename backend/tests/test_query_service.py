"""F3 AI 대화형 탐색 파이프라인 테스트 (COMP-06, UC-05·06, FR-301~412, AI-REQ-15).

AI는 가짜 공급자, 사전 대조·검색 템플릿은 가짜 포트로 시험한다.
"""

import asyncio
from dataclasses import replace

import pytest

from app.data.ai_gateway import DefaultPromptBuilder, ProviderChain, ProviderError
from app.data.ai_gateway.tasks import QueryConditions
from app.data.ai_gateway.testing import FakeProvider
from app.services.query_service import (
    AnchorFilter,
    Choice,
    Filters,
    Option,
    QueryHit,
    QueryInputError,
    QueryService,
    Ref,
    Resolution,
    SearchPage,
    TravelFilter,
    WorkBrief,
    chips,
    evidence_for,
)
from app.services.query_session import SessionStore, merge_conditions
from app.support import request_context

EMPTY = {
    "out_of_scope": False,
    "work_titles": [],
    "persons": [],
    "genres": [],
    "districts": [],
    "release_year_from": None,
    "release_year_to": None,
    "anchor": None,
    "travel": None,
    "result_type": "location",
}


def cond(**kw) -> dict:
    return {**EMPTY, **kw}


HIT_A = QueryHit(1, "자갈치시장", "중구", 35.09, 129.03, (WorkBrief("친구", 2001),), "자갈치역", 5)
HIT_B = QueryHit(2, "범일동 철길", "동구", 35.14, 129.06, (WorkBrief("친구", 2001),))
SUMMARY = {
    "sentences": [
        {
            "text": "친구 촬영지는 자갈치시장과 범일동 철길입니다.",
            "entities": ["친구", "자갈치시장", "범일동 철길"],
        },
        {"text": "자갈치시장은 자갈치역 도보 5분입니다.", "entities": ["자갈치시장", "자갈치역"]},
        {"text": "감천문화마을도 있습니다.", "entities": ["감천문화마을"]},  # 근거 없음 → 삭제
    ]
}


class FakeResolver:
    """작품명·인물명을 그대로 표준 값으로, '서면'은 후보 2개, '없는작품'은 사전에 없음."""

    def __init__(self) -> None:
        self.seen: list[QueryConditions] = []

    async def resolve(self, c: QueryConditions) -> Resolution:
        self.seen.append(c)
        choices, unknown = [], []
        works = []
        for t in c.work_titles:
            if t == "없는작품":
                unknown.append(t)
            else:
                works.append(Ref(f"w:{t}", t))
        anchor = None
        if c.anchor:
            if c.anchor.name == "서면":
                choices.append(
                    Choice(
                        "anchor",
                        "서면",
                        (Option("s1", "서면역(1호선)"), Option("s2", "서면역(2호선)")),
                    )
                )
            ref = Ref(f"st:{c.anchor.name}", f"{c.anchor.name}역") if c.anchor.name else None
            anchor = AnchorFilter(c.anchor.type, None if choices else ref)
        travel = TravelFilter(c.travel.mode, c.travel.max_min) if c.travel else None
        f = Filters(
            works=tuple(works),
            persons=tuple(Ref(f"p:{p}", p) for p in c.persons),
            genres=tuple(c.genres),
            districts=tuple(c.districts),
            year_from=c.release_year_from,
            year_to=c.release_year_to,
            anchor=anchor,
            travel=travel,
            result_type=c.result_type,
        )
        return Resolution(f, tuple(choices), tuple(unknown))

    async def pick(self, choice: Choice, option_id: str) -> Ref:
        option = next(o for o in choice.options if o.id == option_id)
        return Ref(option.id, option.label)


class FakeSearcher:
    """조건마다 결과를 정해 둔다. 기본은 2건, empty_when 조건이 남아 있으면 0건."""

    def __init__(self, *, empty_while: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[Filters, tuple[float, float] | None]] = []
        self.empty_while = empty_while

    async def search(self, filters, *, base_point, limit) -> SearchPage:
        self.calls.append((filters, base_point))
        if any(filters.has(k) for k in self.empty_while):
            return SearchPage(0, ())
        return SearchPage(2, (HIT_A, HIT_B))


async def _no_sleep(_s: float) -> None:
    return None


def make(*replies, searcher=None, sessions=None):
    gemini = FakeProvider("gemini", list(replies))
    chain = ProviderChain({"gemini": gemini}, DefaultPromptBuilder(), sleep=_no_sleep)
    resolver = FakeResolver()
    searcher = searcher or FakeSearcher()
    svc = QueryService(chain, resolver, searcher, sessions=sessions or SessionStore())
    return svc, gemini, resolver, searcher


def run(coro):
    return asyncio.run(coro)


# ── 기본 흐름 ──────────────────────────────────────────────
def test_ask_extracts_searches_and_summarizes() -> None:
    svc, gemini, resolver, searcher = make(cond(work_titles=["친구"]), SUMMARY)

    r = run(svc.ask("친구 촬영지 알려줘"))

    assert r.status == "ok"
    assert [c.label for c in r.chips] == ["작품: 친구"]  # FR-312
    assert (r.total, [h.name for h in r.hits]) == (2, ["자갈치시장", "범일동 철길"])
    assert r.summary == (
        "친구 촬영지는 자갈치시장과 범일동 철길입니다.",
        "자갈치시장은 자갈치역 도보 5분입니다.",
    )  # 근거 없는 문장은 지움(COMP-09)
    assert r.ai_generated and r.session_id
    assert [c.task for c in gemini.calls] == ["extract_query", "summarize_results"]
    assert "검색 결과(근거, 4건)" in gemini.calls[1].user  # 조건 + 결과 수 + 2건


def test_evidence_contains_only_system_values() -> None:
    f = Filters(works=(Ref("w1", "친구"),), districts=("중구",))
    ev = evidence_for([HIT_A, HIT_B], 2, f)

    assert ev.facts == (
        "검색 조건: 작품: 친구 / 구·군: 중구",
        "검색 결과: 2곳",
        "자갈치시장 (중구) — 작품: 친구(2001) — 자갈치역 도보 5분",
        "범일동 철길 (동구) — 작품: 친구(2001)",
    )
    assert {"친구", "자갈치시장", "자갈치역", "중구", "동구"} <= ev.names


def test_masked_query_only_goes_to_ai_and_log() -> None:
    svc, gemini, _, _ = make(cond(), SUMMARY)
    ctx, token = request_context.begin("rid")
    try:
        r = run(svc.ask("010-1234-5678 해운대 촬영지"))
    finally:
        request_context.end(token)

    assert r.masked and "개인정보로 보이는 내용을 가렸습니다" in r.notices
    assert all("010-1234-5678" not in c.user for c in gemini.calls)
    assert "010-1234-5678" not in (ctx.masked_query or "")
    assert ctx.ai_calls == {"gemini": 2}  # 조건 추출 + 요약


@pytest.mark.parametrize("text", ["", "   ", "가" * 201])
def test_input_validation(text: str) -> None:  # FR-301
    svc, gemini, _, _ = make()
    with pytest.raises(QueryInputError):
        run(svc.ask(text))
    assert gemini.calls == []


def test_out_of_scope_is_not_searched() -> None:  # FR-309
    svc, _, resolver, searcher = make(cond(out_of_scope=True))

    r = run(svc.ask("오늘 날씨 어때"))

    assert r.status == "out_of_scope"
    assert resolver.seen == [] and searcher.calls == []


# ── 강등 ──────────────────────────────────────────────────
def test_extract_failure_opens_filter_panel_with_previous_conditions() -> None:  # FR-311
    svc, _, _, searcher = make(cond(districts=["중구"]), SUMMARY, ProviderError("timeout"))
    first = run(svc.ask("중구 촬영지"))

    r = run(svc.ask("그중 영화만", session_id=first.session_id))

    assert r.status == "ai_unavailable"
    assert r.filter_panel.districts == ("중구",)  # 이전 조건 미리 채움
    assert "필터" in r.notices[-1]
    assert len(searcher.calls) == 1  # 강등 때는 검색하지 않고 패널만


def test_summary_failure_shows_results_without_summary() -> None:  # 8a
    svc, *_ = make(cond(), ProviderError("upstream"))

    r = run(svc.ask("촬영지"))

    assert r.status == "ok" and r.total == 2
    assert r.summary == () and not r.ai_generated


def test_zero_results_relax_in_order_then_not_found() -> None:  # FR-305
    searcher = FakeSearcher(empty_while=("works",))  # 작품은 완화하지 않으므로 끝까지 0건
    svc, gemini, _, _ = make(
        cond(
            work_titles=["친구"],
            genres=["드라마"],
            districts=["중구"],
            release_year_from=2000,
            release_year_to=2009,
            anchor={"type": "station", "name": "자갈치"},
            travel={"mode": "walk", "max_min": 10},
        ),
        searcher=searcher,
    )

    r = run(svc.ask("2000년대 친구 드라마 중구 자갈치역 도보 10분"))

    assert r.status == "not_found"
    assert r.relaxed == ("이동 시간", "개봉연도", "장르", "구·군")
    assert len(searcher.calls) == 5
    assert r.summary == () and len(gemini.calls) == 1  # 0건이면 요약을 만들지 않음(근거 게이트)
    assert r.filter_panel.works[0].name == "친구"


def test_relaxation_stops_at_first_hit() -> None:
    searcher = FakeSearcher(empty_while=("travel", "years"))
    svc, *_ = make(
        cond(
            release_year_from=1990,
            release_year_to=1999,
            genres=["액션·모험"],
            anchor={"type": "station", "name": "자갈치"},
            travel={"mode": "walk", "max_min": 5},
        ),
        SUMMARY,
        searcher=searcher,
    )

    r = run(svc.ask("90년대 액션 자갈치역 도보 5분"))

    assert r.status == "ok"
    assert r.relaxed == ("이동 시간", "개봉연도")
    assert [c.key for c in r.chips] == ["genres", "anchor"]  # 장르는 남음


# ── 사전 대조·고르기·칩 ────────────────────────────────────
def test_unknown_name_is_dropped_and_reported() -> None:  # FR-303 4b
    svc, *_ = make(cond(work_titles=["없는작품"], districts=["중구"]), SUMMARY)

    r = run(svc.ask("없는작품 중구"))

    assert r.unknown == ("없는작품",)
    assert [c.key for c in r.chips] == ["districts"]


def test_ambiguous_name_asks_then_searches_after_choice() -> None:  # FR-303 4a
    svc, gemini, _, searcher = make(
        cond(anchor={"type": "station", "name": "서면"}, travel={"mode": "walk", "max_min": 10}),
        SUMMARY,
    )

    r = run(svc.ask("서면역 도보 10분"))
    assert r.status == "choose" and searcher.calls == []
    assert [o.label for o in r.choices[0].options] == ["서면역(1호선)", "서면역(2호선)"]

    r2 = run(svc.choose(r.session_id, "anchor", "s2"))

    assert r2.status == "ok"
    assert searcher.calls[0][0].anchor.ref == Ref("s2", "서면역(2호선)")
    assert "기준: 서면역(2호선)" in [c.label for c in r2.chips]
    assert "서면역 도보 10분" not in gemini.calls[-1].user  # 요약 질문은 해석 조건으로(원문 미보관)


def test_remove_chip_searches_again_without_ai_extract() -> None:  # FR-312
    svc, gemini, _, searcher = make(
        cond(work_titles=["친구"], districts=["중구"]), SUMMARY, SUMMARY
    )
    r = run(svc.ask("중구 친구 촬영지"))

    r2 = run(svc.remove_chip(r.session_id, "districts"))

    assert searcher.calls[-1][0].districts == ()
    assert [c.key for c in r2.chips] == ["works"]
    assert len(gemini.calls) == 3  # 추출 1 + 요약 2(추출을 다시 하지 않음)
    with pytest.raises(QueryInputError):
        run(svc.remove_chip(r.session_id, "persons"))


# ── 기준 위치 ─────────────────────────────────────────────
def test_current_location_without_point_is_dropped() -> None:
    svc, _, _, searcher = make(
        cond(anchor={"type": "current"}, travel={"mode": "walk", "max_min": 15}), SUMMARY
    )

    r = run(svc.ask("내 위치에서 걸어서 15분"))

    assert searcher.calls[0][0].anchor is None and searcher.calls[0][0].travel is None
    assert any("현재 위치" in n for n in r.notices)


def test_current_location_point_goes_only_to_search() -> None:
    svc, gemini, _, searcher = make(
        cond(anchor={"type": "current"}, travel={"mode": "walk", "max_min": 15}), SUMMARY
    )

    r = run(svc.ask("내 위치에서 걸어서 15분", base_point=(35.1, 129.0)))

    assert searcher.calls[0][1] == (35.1, 129.0)
    assert all("35.1" not in c.user for c in gemini.calls)  # AI에는 좌표를 넘기지 않음
    assert "기준: 내 위치" in [c.label for c in r.chips]


# ── 후속 질의·세션 ────────────────────────────────────────
def test_follow_up_keeps_previous_conditions() -> None:  # FR-308
    svc, _, resolver, _ = make(
        cond(persons=["마동석"]), SUMMARY, cond(districts=["해운대구"]), SUMMARY
    )
    first = run(svc.ask("마동석 영화 촬영지"))

    run(svc.ask("그중 해운대구만", session_id=first.session_id))

    assert resolver.seen[-1].persons == ["마동석"]
    assert resolver.seen[-1].districts == ["해운대구"]


def test_new_conversation_forgets_context() -> None:
    svc, _, resolver, _ = make(
        cond(persons=["마동석"]), SUMMARY, cond(districts=["해운대구"]), SUMMARY
    )
    first = run(svc.ask("마동석 영화 촬영지"))
    svc.reset(first.session_id)

    second = run(svc.ask("해운대구 촬영지", session_id=first.session_id))

    assert resolver.seen[-1].persons == []
    assert second.session_id != first.session_id


def test_merge_rules() -> None:
    prev = QueryConditions.model_validate(
        cond(
            persons=["마동석"], genres=["범죄·느와르"], release_year_from=2010, release_year_to=2019
        )
    )
    new = QueryConditions.model_validate(cond(genres=["액션·모험"], release_year_from=2015))

    m = merge_conditions(prev, new)

    assert m.persons == ["마동석"]  # 말하지 않은 항목은 유지
    assert m.genres == ["액션·모험"]  # 말한 항목은 새 값
    assert (m.release_year_from, m.release_year_to) == (2015, None)  # 연도는 한 쌍으로 바꿈


def test_rate_limit_five_per_minute() -> None:  # AI-REQ-15
    now = [0.0]
    sessions = SessionStore(clock=lambda: now[0])
    svc, gemini, _, _ = make(*([cond(), SUMMARY] * 5), sessions=sessions)
    sid = None
    for _ in range(5):
        sid = run(svc.ask("촬영지", session_id=sid)).session_id

    r = run(svc.ask("촬영지", session_id=sid))

    assert r.status == "rate_limited" and 1 <= r.retry_after_s <= 60
    assert len(gemini.calls) == 10
    now[0] = 61.0
    assert sessions.allow_ai_call(sessions.get(sid)) is None


def test_session_expires_after_30_minutes() -> None:
    now = [0.0]
    sessions = SessionStore(clock=lambda: now[0])
    first = sessions.get(None)
    now[0] = 30 * 60 + 1

    assert sessions.get(first.id).id != first.id
    assert len(sessions) == 1


def test_chip_labels() -> None:
    f = Filters(
        persons=(Ref("p", "마동석"),),
        year_from=2010,
        year_to=2019,
        anchor=AnchorFilter("station", Ref("s", "서면역")),
        travel=TravelFilter("subway", 20),
    )
    assert [c.label for c in chips(f)] == [
        "인물: 마동석",
        "개봉: 2010~2019",
        "기준: 서면역",
        "도시철도 20분 이내",
    ]
    assert chips(replace(f, year_from=None))[1].label == "개봉: ~2019"
    assert f.without("anchor").travel is None  # 기준을 빼면 이동 제약도 뺀다

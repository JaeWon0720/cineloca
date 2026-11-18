"""DictionaryResolver 단위 테스트 (FR-403). DB 없이 가짜 사전 조회를 넣어 규칙만 본다."""

import asyncio
from collections.abc import Sequence

import pytest

from app.core.names import norm
from app.data.ai_gateway.tasks import QueryConditions
from app.data.dictionary import Match, Target, district_surfaces, station_surfaces
from app.services.entity_resolver import DictionaryResolver, candidates, label
from app.services.query_service import AnchorFilter, QueryInputError, Ref, TravelFilter

TARGETS = {
    "work": {
        "1": Target("1", "범죄도시", "2017"),
        "2": Target("2", "친구", "2001"),
        "3": Target("3", "넷플릭스 친구", None),
    },
    "person": {"7": Target("7", "마동석", "범죄도시")},
    "station": {"113": Target("113", "부산", "1호선"), "219": Target("219", "서면", "2호선")},
    "location": {"40": Target("40", "영도대교", "영도구"), "41": Target("41", "영도다리 앞", None)},
}
# 정규화한 질의 → 후보. 가짜도 진짜처럼 norm()을 거친 표기로만 찾는다
FOUND = {
    ("work", "범죄도시"): [Match("1", "범죄도시", 1.0, "exact")],
    ("work", "친구"): [Match("2", "친구", 1.0, "exact"), Match("3", "친구", 1.0, "exact")],
    ("person", "마동석"): [Match("7", "마동석", 1.0, "exact")],
    ("station", "부산역"): [Match("113", "부산역", 1.0, "exact")],
    ("location", "영도대교"): [Match("40", "영도대교", 1.0, "exact")],
    ("location", "영도"): [Match("40", "영도대교", 0.75, "trigram"),
                           Match("41", "영도다리앞", 0.72, "trigram")],
}  # fmt: skip


class FakeSource:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def lookup(self, kind: str, text: str) -> list[Match]:
        self.asked.append((kind, text))
        return FOUND.get((kind, norm(text)), [])

    async def lookup_place(self, text: str) -> list[Match]:
        self.asked.append(("location", text))
        return FOUND.get(("location", norm(text)), [])

    async def describe(self, kind: str, ids: Sequence[str]) -> dict[str, Target]:
        return {i: TARGETS[kind][i] for i in ids if i in TARGETS[kind]}


def cond(**kw: object) -> QueryConditions:
    base: dict[str, object] = dict(
        out_of_scope=False,
        work_titles=[],
        persons=[],
        genres=[],
        districts=[],
        release_year_from=None,
        release_year_to=None,
        anchor=None,
        travel=None,
        result_type="location",
    )
    return QueryConditions.model_validate({**base, **kw})


def resolve(**kw: object):
    return asyncio.run(DictionaryResolver(FakeSource()).resolve(cond(**kw)))


def test_names_become_refs_and_other_conditions_pass_through() -> None:
    r = resolve(
        work_titles=["범죄도시"],
        persons=["마 동석"],  # 띄어 써도 같은 정규화로 찾는다
        genres=["범죄·느와르"],
        districts=["해운대구"],
        release_year_from=2010,
        release_year_to=2019,
        result_type="work",
    )
    f = r.filters
    assert f.works == (Ref("1", "범죄도시"),)
    assert f.persons == (Ref("7", "마동석"),)
    assert (f.genres, f.districts) == (("범죄·느와르",), ("해운대구",))
    assert (f.year_from, f.year_to, f.result_type) == (2010, 2019, "work")
    assert r.choices == () and r.unknown == ()


def test_same_title_asks_choice_with_year_labels() -> None:
    r = resolve(work_titles=["친구", "범죄도시"])
    assert r.filters.works == (Ref("1", "범죄도시"),)  # 정해진 것만 조건에 들어간다
    (choice,) = r.choices
    assert (choice.key, choice.name) == ("works", "친구")
    assert [(o.id, o.label) for o in choice.options] == [
        ("work:2", "친구 (2001)"),
        ("work:3", "넷플릭스 친구"),
    ]


def test_unknown_names_are_dropped_and_reported() -> None:
    r = resolve(work_titles=["없는작품", "범죄도시"], persons=["아무개"])
    assert r.filters.works == (Ref("1", "범죄도시"),)
    assert r.filters.persons == ()
    assert r.unknown == ("없는작품", "아무개")


def test_duplicate_names_resolve_once() -> None:
    r = resolve(work_titles=["범죄도시", "범죄 도시", "범죄도시"])
    assert r.filters.works == (Ref("1", "범죄도시"),)


def test_station_without_name_means_any_station() -> None:
    r = resolve(anchor={"type": "station", "name": None}, travel={"mode": "walk", "max_min": 10})
    assert r.filters.anchor == AnchorFilter("station", None)
    assert r.filters.travel == TravelFilter("walk", 10)
    assert r.choices == ()


def test_named_station_and_current_anchor() -> None:
    r = resolve(anchor={"type": "station", "name": "부산역"})
    assert r.filters.anchor == AnchorFilter("station", Ref("113", "부산역"))
    r = resolve(anchor={"type": "current"}, travel={"mode": "walk", "max_min": 15})
    assert r.filters.anchor == AnchorFilter("current")
    assert r.filters.travel == TravelFilter("walk", 15)


def test_place_anchor_uses_location_names_then_station() -> None:
    r = resolve(anchor={"type": "place", "name": "영도대교"})
    assert r.filters.anchor == AnchorFilter("place", Ref("40", "영도대교"))
    r = resolve(anchor={"type": "place", "name": "부산역"})  # 촬영지에 없으면 역에서 찾는다
    assert r.filters.anchor == AnchorFilter("station", Ref("113", "부산역"))


def test_close_place_candidates_ask_choice_and_keep_anchor_type() -> None:
    r = resolve(anchor={"type": "place", "name": "영도"}, travel={"mode": "walk", "max_min": 10})
    assert r.filters.anchor == AnchorFilter("place", None)
    assert r.filters.travel == TravelFilter("walk", 10)
    (choice,) = r.choices
    assert choice.key == "anchor"
    assert [(o.id, o.label) for o in choice.options] == [
        ("location:40", "영도대교 (영도구)"),
        ("location:41", "영도다리 앞"),
    ]


def test_unknown_anchor_drops_travel_too() -> None:
    r = resolve(anchor={"type": "station", "name": "없는역"}, travel={"mode": "walk", "max_min": 5})
    assert r.filters.anchor is None and r.filters.travel is None
    assert r.unknown == ("없는역",)
    r = resolve(travel={"mode": "walk", "max_min": 5})  # 기준 없는 이동 조건은 뜻이 없다
    assert r.filters.travel is None


def test_pick_returns_ref_of_chosen_option() -> None:
    resolver = DictionaryResolver(FakeSource())
    choice = asyncio.run(resolver.resolve(cond(work_titles=["친구"]))).choices[0]
    assert asyncio.run(resolver.pick(choice, "work:2")) == Ref("2", "친구")
    with pytest.raises(QueryInputError):
        asyncio.run(resolver.pick(choice, "work:1"))  # 후보에 없던 값


def test_candidates_keeps_only_scores_near_the_top() -> None:
    a, b, c = (Match("1", "가", 0.80, "trigram"), Match("2", "나", 0.76, "trigram"),
               Match("3", "다", 0.74, "trigram"))  # fmt: skip
    assert candidates([a, b, c]) == [a, b]  # 0.05 안만 같은 후보
    assert candidates([a, c]) == [a]
    assert candidates([a, Match("1", "가2", 0.79, "trigram")]) == [a]  # 같은 표준 값은 한 번
    assert candidates([]) == []


def test_labels_and_surfaces() -> None:
    assert label("station", Target("219", "서면", "2호선")) == "서면역 (2호선)"
    assert label("work", Target("9", "무제", None)) == "무제"
    assert label("station", Target("205", "벡스코(시립미술관)", "2호선")) == "벡스코역 (2호선)"
    assert label("station", Target("1", "부산역", None)) == "부산역"
    assert station_surfaces("서면") == ["서면", "서면역"]
    assert station_surfaces("부산역") == ["부산", "부산역"]
    surfaces = dict(district_surfaces())
    assert surfaces["해운대"] == "해운대구" and surfaces["기장"] == "기장군"
    assert len({name for _, name in district_surfaces()}) == 16
    # '구'를 떼면 한 글자가 되는 곳은 짧은 이름을 두지 않는다
    assert not {"중", "서", "동", "남", "북"} & set(surfaces)

"""TemplateSearcher 단위 테스트 (FR-304, ADR-010). DB 없이 템플릿 선택과 매개변수 바인딩만 본다."""

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.core.genres import GENRE_GROUPS
from app.data import search_templates as repo
from app.services.query_service import AnchorFilter, Filters, QueryInputError, Ref, TravelFilter
from app.services.template_searcher import (
    DEFAULT_SUBWAY_MIN,
    DEFAULT_WALK_MIN,
    Plan,
    TemplateSearcher,
    conditions,
    plan,
    walk_radius_m,
)

STATION = AnchorFilter("station", Ref("119", "서면역"))
PLACE = AnchorFilter("place", Ref("7", "영도대교"))
WALK10 = TravelFilter("walk", 10)
SUBWAY20 = TravelFilter("subway", 20)


def pick(anchor=None, travel=None, *, has_base_point=False, **kw) -> Plan:
    return plan(Filters(anchor=anchor, travel=travel, **kw), has_base_point=has_base_point)


def test_walk_radius_rule() -> None:
    # 반경(m) = N × 4000/60 ÷ 1.3
    assert walk_radius_m(10) == pytest.approx(512.82, abs=0.01)
    assert walk_radius_m(30) == pytest.approx(1538.46, abs=0.01)


def test_plan_without_anchor_is_t1() -> None:
    assert pick() == Plan("T1_FILTER")
    assert pick(travel=WALK10) == Plan("T1_FILTER")  # 기준 없는 이동 조건은 쓰지 않는다
    assert pick(result_type="work") == Plan("T1_FILTER")  # 작품 결과는 search_works()가 맡는다


def test_plan_any_station_is_t3_walk() -> None:
    any_station = AnchorFilter("station")
    assert pick(any_station, WALK10) == Plan("T3_NEAR_STATION", minutes=10)
    assert pick(any_station) == Plan("T3_NEAR_STATION", minutes=DEFAULT_WALK_MIN)
    assert pick(any_station, TravelFilter("walk")).minutes == DEFAULT_WALK_MIN


def test_plan_walk_from_point_is_t2() -> None:
    r10 = walk_radius_m(10)
    assert pick(STATION, WALK10) == Plan("T2_NEAR_POINT", "station", "119", radius_m=r10)
    assert pick(PLACE, WALK10) == Plan("T2_NEAR_POINT", "location", "7", radius_m=r10)
    assert pick(PLACE) == Plan("T2_NEAR_POINT", "location", "7", radius_m=1000.0)  # 기본 1km
    current = pick(AnchorFilter("current"), WALK10, has_base_point=True)
    assert current == Plan("T2_NEAR_POINT", "current", None, radius_m=r10)


def test_plan_subway_is_t3_from_station() -> None:
    assert pick(STATION, SUBWAY20) == Plan("T3_NEAR_STATION", minutes=20, base_station="119")
    assert pick(STATION, TravelFilter("subway")).minutes == DEFAULT_SUBWAY_MIN
    # 장소·현재 위치에서 도시철도: 기준점의 최근접 역에서 출발
    assert pick(PLACE, SUBWAY20) == Plan(
        "T3_NEAR_STATION", "location", "7", minutes=20, via_nearest_station=True
    )
    assert pick(AnchorFilter("current"), SUBWAY20, has_base_point=True) == Plan(
        "T3_NEAR_STATION", "current", None, minutes=20, via_nearest_station=True
    )


def test_plan_falls_back_to_t1_when_point_is_unknown() -> None:
    assert pick(AnchorFilter("current"), WALK10, has_base_point=False) == Plan("T1_FILTER")
    assert pick(AnchorFilter("place"), WALK10) == Plan("T1_FILTER")  # 고르기 전(ref 없음)


def test_conditions_use_none_for_no_condition_and_expand_genre_groups() -> None:
    assert conditions(Filters()).args() == (None, None, None, None, None, None)
    f = Filters(
        works=(Ref("3", "해운대"),),
        persons=(Ref("12", "가"), Ref("15", "나")),
        genres=("재난", "공포"),
        districts=("해운대구",),
        year_from=2000,
        year_to=2010,
    )
    kmdb = sorted({*GENRE_GROUPS["재난"], *GENRE_GROUPS["공포"]})
    assert conditions(f).args() == ([3], [12, 15], kmdb, ["해운대구"], 2000, 2010)
    with pytest.raises(QueryInputError):
        conditions(Filters(works=(Ref("3; DROP TABLE work", "x"),)))


def test_templates_are_fixed_text_with_numbered_placeholders_only() -> None:
    counts = {"T1_FILTER": 7, "T2_NEAR_POINT": 10, "T3_NEAR_STATION": 9, "T4_WORKS": 7}
    for name, sql in {**repo.TEMPLATES, "WORKS": repo.WORKS_OF_HITS}.items():
        assert "%" not in sql and "{" not in sql and "'" not in sql, name
        used = {int(n) for n in re.findall(r"\$(\d+)", sql)}
        assert used == set(range(1, counts.get(name, 7) + 1)), name
        assert "l.lat IS NOT NULL" in sql  # 좌표 없는 촬영지 제외


class RecordingConn:
    """fetch·fetchrow가 받은 (질의문, 값)을 적어 둔다. 결과는 비어 있다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args: object) -> list:
        self.calls.append((sql, args))
        return []

    async def fetchrow(self, sql: str, *args: object) -> dict:
        self.calls.append((sql, args))
        return {"lat": 35.1, "lon": 129.0, "id": "119", "dist_m": 100.0}


def run_search(filters: Filters, base_point=None, limit=20) -> RecordingConn:
    conn = RecordingConn()

    @asynccontextmanager
    async def connect() -> AsyncIterator[RecordingConn]:
        yield conn

    page = asyncio.run(
        TemplateSearcher(connect).search(filters, base_point=base_point, limit=limit)  # type: ignore[arg-type]
    )
    assert (page.total, page.hits) == (0, ())
    return conn


def test_values_reach_sql_only_as_bound_parameters(caplog: pytest.LogCaptureFixture) -> None:
    hostile = "해운대구'; DROP TABLE location; --"
    cases = [
        (Filters(districts=(hostile,)), None, repo.T1_FILTER),
        (Filters(districts=(hostile,), anchor=STATION, travel=WALK10), None, repo.T2_NEAR_POINT),
        (Filters(districts=(hostile,), anchor=AnchorFilter("station"), travel=WALK10), None,
         repo.T3_NEAR_STATION),
        (Filters(anchor=AnchorFilter("station", Ref(hostile, "x")), travel=SUBWAY20), None,
         repo.T3_NEAR_STATION),
        (Filters(anchor=AnchorFilter("current"), travel=WALK10), (35.123456, 129.123456),
         repo.T2_NEAR_POINT),
    ]  # fmt: skip
    fixed = {*repo.TEMPLATES.values(), repo.WORKS_OF_HITS, *repo._POINT.values()}
    with caplog.at_level(logging.DEBUG):
        for filters, base_point, expected in cases:
            conn = run_search(filters, base_point)
            sql, args = conn.calls[-1]
            assert sql is expected
            assert all(s in fixed and hostile not in s for s, _ in conn.calls)
            flat = [x for a in args for x in (a if isinstance(a, list) else [a])]
            if base_point is None:
                assert hostile in flat
            else:
                assert args[6:9] == (35.123456, 129.123456, walk_radius_m(10))
    # 기준점 좌표는 로그에 남기지 않는다(BR-10)
    assert "35.123456" not in caplog.text and "129.123456" not in caplog.text


def test_limit_is_clamped_to_allowed_range() -> None:
    assert run_search(Filters(), limit=500).calls[-1][1][-1] == repo.MAX_LIMIT
    assert run_search(Filters(), limit=0).calls[-1][1][-1] == 1
    assert run_search(Filters(), limit=7).calls[-1][1][-1] == 7

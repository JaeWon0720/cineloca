"""규칙 태그 테스트 (FR-607, app/core/place_tags.py rule_tags · app/data/rule_tags.py).

장소명은 착수 PoC 촬영지 목록에 있는 이름 형식을 본떴다.
"""

import asyncio

import pytest

from app.core.place_tags import PLACE_TYPES, RULE_KEYWORDS, rule_tags
from app.data import rule_tags as rule_tags_db


@pytest.mark.parametrize(
    ("name", "tags"),
    [
        ("해운대해수욕장", ["해변"]),
        ("해운대해수욕장 삼거리", ["해변", "거리·도로"]),
        ("부산역(기차)", ["역·교통"]),  # 괄호 안은 보지 않음
        ("구) 좌천역(기차)", ["역·교통"]),  # 없어진 곳은 원래 쓰임으로
        ("[폐업] 위모텔(구서동)", ["숙박"]),
        ("[철거]해운대 포장마차촌", ["식당·카페"]),
        ("부산항 국제여객터미널", ["항구", "역·교통"]),
        ("김해공항", ["역·교통"]),  # '공항'은 항구가 아님
        ("황령산", ["공원·산"]),
        ("초량밀면(초량동)", ["식당·카페"]),
        ("구) 천가초등학교 천성분교", ["학교"]),
        ("부산고등학교", ["학교"]),
        ("영도대교", ["다리"]),
        ("자갈치시장", ["시장"]),
        ("해운대요양병원 부근 도로", ["거리·도로"]),  # '부근' 뒤가 그 장소
        ("봉래초등학교 부근 육교", ["다리"]),
        ("부산대교 부근", ["다리"]),  # '부근' 뒤가 비면 앞을 봄
    ],
)
def test_rule_tags_from_place_name(name: str, tags: list[str]) -> None:
    assert rule_tags(name) == tags


@pytest.mark.parametrize(
    "name",
    ["부산영화촬영스튜디오", "부산아쿠아리움", "해동수산", "지역아동센터", "역사관", "", None],
)
def test_rule_tags_no_false_match(name: str | None) -> None:
    # '부산'의 산, '역사'의 역, '수산'의 산처럼 한 글자 키워드가 낱말 중간에 있으면 맞추지 않는다
    assert rule_tags(name) == []


def test_rule_keywords_use_only_listed_types() -> None:
    assert list(RULE_KEYWORDS) == list(PLACE_TYPES)  # 목록에 없는 태그는 붙일 수 없다


class FakeConn:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.sql: list[str] = []
        self.updates: list[tuple[int, list[str]]] = []

    async def fetch(self, sql: str) -> list[dict]:
        self.sql.append(sql)
        return self.rows

    async def executemany(self, sql: str, args: list[tuple[int, list[str]]]) -> None:
        self.sql.append(sql)
        self.updates = list(args)


def test_apply_tags_only_untagged_locations() -> None:
    conn = FakeConn([{"id": 1, "name": "자갈치시장"}, {"id": 2, "name": "부산아쿠아리움"}])
    stats = asyncio.run(rule_tags_db.apply(conn))
    assert stats == {"checked": 2, "tagged": 1}
    assert conn.updates == [(1, ["시장"])]
    # 관리자가 고른 태그가 있는 곳은 읽지도, 덮지도 않는다(FR-609·610)
    assert "cardinality(tags) = 0" in conn.sql[0] and "cardinality(tags) = 0" in conn.sql[1]


def test_apply_nothing_to_tag_runs_no_update() -> None:
    conn = FakeConn([{"id": 2, "name": "부산아쿠아리움"}])
    assert asyncio.run(rule_tags_db.apply(conn)) == {"checked": 1, "tagged": 0}
    assert len(conn.sql) == 1

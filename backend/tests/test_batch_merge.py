"""정규화·병합 단위 테스트 (FR-602~604). PoC p2와 같은 규칙인지 확인한다."""

from app.batch.merge import district_of, merge, norm, year_of


def test_norm_drops_brackets_space_punct() -> None:
    assert norm(" 해운대 (2009) ") == "해운대"
    assert norm("범죄와의 전쟁: 나쁜놈들 전성시대") == "범죄와의전쟁나쁜놈들전성시대"
    assert norm(None) == ""


def test_district_of() -> None:
    assert district_of("부산광역시 해운대구 우동 1") == "해운대구"
    assert district_of("부산 부산진구 전포대로 1") == "부산진구"
    assert district_of("부산광역시 중구 광복로 1") == "중구"
    assert district_of("서울특별시 중구 세종대로 1") is None
    assert district_of(None) is None


def test_merge_joins_same_names_and_keeps_record_per_source() -> None:
    rows = {
        "DATA-01": [
            {"opusNm": "해운대", "plcNm": "해운대 해수욕장", "opnDay": "2009-07-22"},
            {"opusNm": "해운대 ", "plcNm": "해운대해수욕장", "roadNmAddr": "부산 해운대구 우동"},
        ],
        "DATA-02": [{"opusNm": "해운대", "plcNm": "해운대해수욕장"}, {"opusNm": "", "plcNm": "x"}],
    }
    m = merge(rows)
    assert list(m.works) == ["해운대"] and m.works["해운대"].release_year == 2009
    assert list(m.places) == ["해운대해수욕장"]
    assert m.places["해운대해수욕장"].address == "부산 해운대구 우동"  # 빈 값은 뒤 값으로 채움
    assert m.places["해운대해수욕장"].gu == "해운대구"
    assert set(m.shootings) == {("해운대", "해운대해수욕장", s) for s in ("DATA-01", "DATA-02")}


def test_year_of() -> None:
    assert year_of("2009-07-22") == 2009
    assert year_of("20171227") == 2017
    assert year_of(None) is None and year_of("미정") is None

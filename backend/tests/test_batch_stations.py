"""도시철도역 호선·최근접 역 단위 테스트 (FR-606, BR-12)."""

from app.batch import stations


def test_line_of() -> None:
    assert stations.line_of("95") == "1호선" and stations.line_of("134") == "1호선"
    assert stations.line_of("201") == "2호선" and stations.line_of("301") == "3호선"
    assert stations.line_of("402") == "4호선" and stations.line_of("801") == "동해선"
    assert stations.line_of("901") == "부산김해경전철"
    assert stations.line_of("500") is None and stations.line_of("abc") is None


def test_nearest_and_walk_min() -> None:
    a = stations.Station("101", "가", "1호선", 35.1000, 129.0000)
    b = stations.Station("102", "나", "1호선", 35.2000, 129.0000)
    st, minutes = stations.nearest(35.1009, 129.0, [b, a])
    assert st is a
    d = stations.distance_m(35.1009, 129.0, 35.1, 129.0)
    assert 95 < d < 105 and minutes == stations.walk_min(d) == round(d * 1.3 / (4000 / 60), 1)
    assert stations.nearest(35.1, 129.0, []) is None

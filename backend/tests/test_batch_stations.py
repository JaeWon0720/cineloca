"""좌표 보조 함수와 최근접 역 단위 테스트 (FR-605·606, BR-12). 외부 호출 없음."""

import pytest

from app.batch import stations
from app.batch.geocode import clean_keyword, in_busan, trim_tail, utmk_to_wgs84


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        ("부산광역시 해운대구 센텀5로 55 1층 카페", "부산광역시 해운대구 센텀5로 55"),
        ("부산 기장군 일광읍 기룡리 630 일광해변", "부산 기장군 일광읍 기룡리 630"),
        ("부산 사하구 다대동 산 1-2 몰운대", "부산 사하구 다대동 산 1-2"),
        ("부산광역시 중구 광복로 55-1", None),
    ],
)
def test_trim_tail(raw: str, short: str | None) -> None:
    assert trim_tail(raw) == short


def test_clean_keyword_removes_forbidden_and_brackets() -> None:
    assert clean_keyword("부산 중구 (옛 미문화원) 대청로; 99'") == "부산 중구 대청로 99"
    assert len(clean_keyword("가" * 100)) == 40


def test_utmk_to_wgs84_round_trip_busan_city_hall() -> None:
    from pyproj import Transformer

    x, y = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True).transform(
        129.0756, 35.1796
    )
    lat, lon = utmk_to_wgs84(x, y)
    assert (lat, lon) == pytest.approx((35.1796, 129.0756), abs=1e-5)
    assert in_busan(lat, lon)
    assert not in_busan(37.56, 126.97)


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

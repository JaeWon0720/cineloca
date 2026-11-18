"""촬영지 좌표 단위 테스트 (FR-605, #25). 가짜 HTTP 응답만 쓴다(실제 주소·카카오 API 호출 없음).

카카오 응답은 저장 금지(DATA-07)라 실제 응답 사본 대신 같은 필드 이름으로 손으로 만든 값을 쓴다.
"""

import asyncio

import httpx
import pytest
from pyproj import Transformer

from app.batch.geocode import (
    Geocoder,
    Hints,
    JusoGeocoder,
    KakaoKeywordGeocoder,
    choose,
    clean_keyword,
    dong_key,
    hints,
    in_busan,
    keyword_geocoder,
    precise,
    trim_tail,
    utmk_to_wgs84,
)
from app.core.config import get_settings

HIT = {
    "siNm": "부산광역시",
    "sggNm": "해운대구",
    "roadAddr": "부산광역시 해운대구 센텀5로 55",
    "admCd": "2635010500",
    "rnMgtSn": "263504000000",
    "udrtYn": "0",
    "buldMnnm": "55",
    "buldSlno": "0",
}
COORD = {"entX": "1144380.1", "entY": "1688520.2"}


def ok(juso: list[dict]) -> dict:
    return {"results": {"common": {"errorCode": "0"}, "juso": juso}}


def juso(handler) -> JusoGeocoder:
    async def no_sleep(_: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JusoGeocoder(http, "S", "C", "http://juso.test/addrlink", sleep=no_sleep)


def doc(name: str, addr: str, lat: float = 35.1, lon: float = 129.1) -> dict:
    return {"place_name": name, "address_name": addr, "y": str(lat), "x": str(lon)}


@pytest.mark.parametrize(
    ("addr", "want"),
    [
        ("부산 해운대구 센텀5로 55", True),
        ("부산 기장군 일광읍 기룡리 630", True),
        ("부산 해운대구 중동", False),  # 동까지만(#25: 청사포 철길 → 아쿠아리움)
        ("부산 남구 용호동 (2번 출구 앞)", False),  # 괄호 안 숫자는 번지가 아니다
        (None, False),
    ],
)
def test_precise(addr: str | None, want: bool) -> None:
    assert precise(addr) is want


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        ("부산 해운대구 센텀5로 55 1층 카페", "부산 해운대구 센텀5로 55"),
        ("부산 기장군 일광읍 기룡리 630 일광해변", "부산 기장군 일광읍 기룡리 630"),
        ("부산광역시 중구 광복로 55-1", None),
    ],
)
def test_trim_tail(raw: str, short: str | None) -> None:
    assert trim_tail(raw) == short


def test_clean_keyword_removes_forbidden_and_brackets() -> None:
    assert clean_keyword("부산 중구 (옛 미문화원) 대청로; 99'") == "부산 중구 대청로 99"
    assert len(clean_keyword("가" * 100)) == 40


def test_utmk_to_wgs84_round_trip_busan_city_hall() -> None:
    to_utmk = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    lat, lon = utmk_to_wgs84(*to_utmk.transform(129.0756, 35.1796))
    assert (lat, lon) == pytest.approx((35.1796, 129.0756), abs=1e-5)
    assert in_busan(lat, lon) and not in_busan(37.56, 126.97)


def test_juso_trims_tail_then_converts_coord() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            kw = req.url.params["keyword"]
            seen.append(kw)
            return httpx.Response(200, json=ok([HIT] if kw.endswith("55") else []))
        assert req.url.params["confmKey"] == "C"  # 좌표 API는 좌표용 키
        return httpx.Response(200, json=ok([COORD]))

    g = asyncio.run(juso(handler).geocode("부산광역시 해운대구 센텀5로 55 1층 카페"))
    assert seen == ["부산광역시 해운대구 센텀5로 55 1층 카페", "부산광역시 해운대구 센텀5로 55"]
    assert g is not None and g.via == "address" and 35.0 < g.lat < 35.4


def test_juso_skips_imprecise_and_other_district() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            return httpx.Response(200, json=ok([{**HIT, "sggNm": "수영구"}]))
        raise AssertionError("구가 다른 결과는 좌표를 부르지 않는다")

    g = juso(handler)
    assert asyncio.run(g.geocode("부산 해운대구 중동")) is None  # 호출 없이 끝남
    assert asyncio.run(g.geocode("부산 해운대구 센텀5로 55")) is None


def test_juso_busy_retried_on_coord_api_too() -> None:
    calls = {"coord": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            return httpx.Response(200, json=ok([HIT]))
        calls["coord"] += 1
        if calls["coord"] < 3:
            return httpx.Response(200, json={"results": {"common": {"errorCode": "E0007"}}})
        return httpx.Response(200, json=ok([COORD]))

    assert asyncio.run(juso(handler).geocode("부산광역시 해운대구 센텀5로 55")) is not None
    assert calls["coord"] == 3


def test_dong_key_and_hints() -> None:
    assert dong_key("우1동") == "우동" and dong_key("중앙동3가") == "중앙동"
    assert dong_key("정관읍") == "정관읍" and dong_key("센텀") is None
    assert hints("청사포 철길", "부산 해운대구 중동") == Hints("해운대구", "중동", None)
    assert hints("세화병원(온천동)", None) == Hints(None, "온천동", None)
    assert hints("우1동 치안센터", None) == Hints(None, "우동", None)
    assert hints("대진공업(양산)", None) == Hints(None, None, "양산")


def test_choose_exact_name_in_same_district() -> None:
    h = Hints("남구", "용호동")
    docs = [
        doc("오륙도아파트", "부산 남구 감만동 1", 35.11, 129.08),
        doc("오륙도", "부산 남구 용호동 936", 35.0979, 129.1241),
    ]
    assert choose(docs, "오륙도", h) == (35.0979, 129.1241)
    assert choose(docs, "오륙도", Hints("해운대구")) is None  # 구가 다르면 버림


def test_choose_rejects_longer_result_name_and_far_namesakes() -> None:
    near = Hints("수영구", "남천동")
    assert choose([doc("황령산보쌈", "부산 수영구 남천동 48")], "황령산", near) is None
    assert choose([doc("산엔청", "부산 사하구 당리동 305")], "산엔청 고기집", Hints(None, "당리동"))
    two = [doc("대궁삼계탕", "부산 중구 중앙동3가 1", 35.10, 129.03)]
    two.append(doc("대궁삼계탕", "부산 서구 부민동1가 2", 35.10, 129.02))
    assert choose(two, "대궁삼계탕", Hints()) is None  # 동명 장소 두 곳
    assert choose(two, "대궁삼계탕", Hints(None, "중앙동")) == (35.1, 129.03)


def test_choose_area_hint_outside_busan_is_dropped() -> None:
    d = [doc("대진공업", "부산 강서구 생곡동 1581")]
    assert choose(d, "대진공업", hints("대진공업(양산)", None)) is None
    g = [doc("서희스타힐스아파트", "부산 기장군 정관읍 방곡리 385", 35.32, 129.18)]
    assert choose(g, "서희스타힐스아파트", hints("서희스타힐스아파트(정관)", None))


def test_kakao_query_and_key_header() -> None:
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["q"], seen["auth"] = req.url.params["query"], req.headers["Authorization"]
        return httpx.Response(200, json={"documents": [doc("청사포", "부산 해운대구 중동 1")]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    g = asyncio.run(KakaoKeywordGeocoder(http, "K").geocode("청사포", "부산 해운대구 중동"))
    assert seen == {"q": "해운대구 중동 청사포", "auth": "KakaoAK K"}
    assert g is not None and g.via == "keyword"


def no_call(req: httpx.Request) -> httpx.Response:
    raise AssertionError(f"부르면 안 됨: {req.url}")


def test_kakao_keyword_setting_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw, want in ((None, False), ("false", False), ("true", True), ("1", True)):
        if raw is None:
            monkeypatch.delenv("GEOCODE_KAKAO_KEYWORD", raising=False)
        else:
            monkeypatch.setenv("GEOCODE_KAKAO_KEYWORD", raw)
        get_settings.cache_clear()
        assert get_settings().geocode_kakao_keyword is want
    get_settings.cache_clear()


def test_keyword_path_off_unless_enabled_and_key() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(no_call))
    assert keyword_geocoder(http, False, "K") is None
    assert keyword_geocoder(http, True, None) is None
    assert isinstance(keyword_geocoder(http, True, "K"), KakaoKeywordGeocoder)


def test_locate_off_leaves_imprecise_unlocated() -> None:
    """기본(꺼짐): 주소 없거나 동까지만 있으면 어떤 API도 부르지 않고 '위치 미확인'."""
    g = Geocoder(juso(no_call), keyword_geocoder(httpx.AsyncClient(), False, "K"))
    assert asyncio.run(g.locate("청사포 철길", "부산 해운대구 중동")) is None
    assert asyncio.run(g.locate("오륙도", None)) is None


def test_locate_on_uses_keyword_only_for_imprecise() -> None:
    def kakao(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"documents": [doc("청사포", "부산 해운대구 중동 1")]})

    def juso_api(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            return httpx.Response(200, json=ok([HIT]))
        return httpx.Response(200, json=ok([COORD]))

    kw = keyword_geocoder(httpx.AsyncClient(transport=httpx.MockTransport(kakao)), True, "K")
    g = Geocoder(juso(juso_api), kw)
    near = asyncio.run(g.locate("청사포", "부산 해운대구 중동"))
    assert near is not None and near.via == "keyword"
    exact = asyncio.run(g.locate("영화의전당", "부산 해운대구 센텀5로 55"))
    assert exact is not None and exact.via == "address"

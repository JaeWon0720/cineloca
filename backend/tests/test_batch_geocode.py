"""주소 → 좌표 단위 테스트 (FR-605). 가짜 HTTP 응답만 쓴다(실제 주소 API 호출 없음)."""

import asyncio

import httpx

from app.batch.geocode import JusoGeocoder, precise

HIT = {
    "siNm": "부산광역시",
    "roadAddr": "부산광역시 해운대구 센텀5로 55",
    "admCd": "2635010500",
    "rnMgtSn": "263504000000",
    "udrtYn": "0",
    "buldMnnm": "55",
    "buldSlno": "0",
}


def ok(juso: list[dict]) -> dict:
    return {"results": {"common": {"errorCode": "0"}, "juso": juso}}


def make(handler) -> JusoGeocoder:
    async def no_sleep(_: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return JusoGeocoder(http, "S", "C", "http://juso.test/addrlink", sleep=no_sleep)


def test_trims_tail_then_converts_coord() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            kw = req.url.params["keyword"]
            seen.append(kw)
            return httpx.Response(200, json=ok([HIT] if kw.endswith("55") else []))
        assert req.url.params["confmKey"] == "C"  # 좌표 API는 좌표용 키
        return httpx.Response(200, json=ok([{"entX": "1144380.1", "entY": "1688520.2"}]))

    g = asyncio.run(make(handler).geocode("부산광역시 해운대구 센텀5로 55 1층 카페"))
    assert seen == ["부산광역시 해운대구 센텀5로 55 1층 카페", "부산광역시 해운대구 센텀5로 55"]
    assert g is not None and g.via == "address" and 35.0 < g.lat < 35.4


def test_busy_retried_on_coord_api_too() -> None:
    calls = {"coord": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            return httpx.Response(200, json=ok([HIT]))
        calls["coord"] += 1
        if calls["coord"] < 3:
            return httpx.Response(200, json={"results": {"common": {"errorCode": "E0007"}}})
        return httpx.Response(200, json=ok([{"entX": "1144380.1", "entY": "1688520.2"}]))

    assert asyncio.run(make(handler).geocode("부산광역시 해운대구 센텀5로 55")) is not None
    assert calls["coord"] == 3


def test_non_busan_result_dropped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("addrLinkApi.do"):
            return httpx.Response(200, json=ok([{**HIT, "siNm": "서울특별시"}]))
        raise AssertionError("부산 밖 주소는 좌표를 부르지 않는다")

    assert asyncio.run(make(handler).geocode("서울특별시 중구 세종대로 110")) is None


def test_untrusted_addresses_not_searched() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("동까지만 있는 주소·주소 없음은 API를 부르지 않는다(#25)")

    g = make(handler)
    assert asyncio.run(g.geocode("부산 해운대구 중동")) is None
    assert asyncio.run(g.geocode("부산 해운대구 중동 (청사포 123번 버스 종점)")) is None
    assert asyncio.run(g.geocode(None)) is None


def test_precise() -> None:
    assert precise("부산 해운대구 센텀5로 55")
    assert precise("부산 사하구 다대동 산 1-2")
    assert not precise("부산 해운대구 중동")
    assert not precise("부산 중구 남포동 (2층)")
    assert not precise(None) and not precise("")

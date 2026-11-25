"""주변 시설 후보 검색 테스트 (FR-414·418, TourAPI NO_STORE). 네트워크 없이 가짜 응답을 쓴다."""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from app.core.amenities import category_of, is_excluded
from app.data.tourapi import MAX_PAGES, PAGE_ROWS, TourApiClient, TourApiError, _parse
from app.services.amenity_finder import AmenityFinder, Stop, midpoint

M = 1 / 111_195  # 위도 1m


def item(cid: str, lat: float, lon: float, lcls: str | None = "FD010100", title: str = "") -> dict:
    return {
        "contentid": cid,
        "title": title or f"시설{cid}",
        "addr1": "부산광역시 중구",
        "mapy": str(lat),
        "mapx": str(lon),
        "dist": "0",
        "lclsSystm3": lcls,
    }


def ok(*items: dict) -> dict:
    return {
        "response": {
            "header": {"resultCode": "0000"},
            "body": {"items": {"item": list(items)} if items else "", "totalCount": len(items)},
        }
    }


class FakeGet:
    def __init__(self, reply) -> None:  # reply(params) → JSON 또는 TourApiError
        self.reply = reply
        self.calls: list[dict] = []

    async def __call__(self, url: str, params: Mapping[str, Any], *, timeout_s: float) -> Any:
        self.calls.append({**params, "timeout_s": timeout_s})
        out = self.reply(params)
        if isinstance(out, Exception):
            raise out
        return out


A, B, C = Stop(1, 35.10, 129.03), Stop(2, 35.11, 129.03), Stop(3, 35.11, 129.04)


def finder(reply) -> tuple[AmenityFinder, FakeGet]:
    get = FakeGet(reply)
    return AmenityFinder(TourApiClient("test-key", get=get)), get


@pytest.mark.parametrize(
    ("facility", "code", "category"),
    [
        ("food", "FD010100", "밥"),
        ("food", "FD020300", "밥"),  # 서양식
        ("food", "FD030300", "밥"),  # 치킨
        ("food", "FD030100", "카페"),  # 제과는 카페
        ("food", "FD050200", "카페"),  # 찻집
        ("food", "FD040100", None),  # 주점
        ("food", None, None),
        ("lodging", "AC010100", "호텔"),
        ("lodging", "AC020200", "콘도·레지던스"),
        ("lodging", "AC030300", "펜션·민박"),
        ("lodging", "AC060200", "게스트하우스·호스텔"),
        ("lodging", "FD010100", None),
    ],
)
def test_category_mapping(facility: str, code: str | None, category: str | None) -> None:
    assert category_of(facility, code) == category  # type: ignore[arg-type]


def test_fetches_busan_list_once_per_kind_and_keeps_search_order() -> None:
    f, get = finder(lambda p: ok())

    result = asyncio.run(f.find([A, B, C]))

    assert [(r.kind, r.index, r.from_location_id, r.to_location_id) for r in result] == [
        ("segment", 0, 1, 2),
        ("segment", 1, 2, 3),
        ("lodging", 2, 3, None),
    ]
    # 위치기반 조회는 법정동 코드로 옮겨진 항목을 빠뜨려 부산 목록을 종류마다 한 번씩만 받는다
    assert sorted(c["contentTypeId"] for c in get.calls) == [32, 39]
    assert all(
        c["lDongRegnCd"] == "26" and c["pageNo"] == 1 and c["numOfRows"] == PAGE_ROWS
        and c["timeout_s"] == 3.0 and "mapX" not in c
        for c in get.calls
    )  # fmt: skip
    assert {r.status for r in result} == {"empty"}


def test_each_segment_filters_the_shared_list_by_its_own_center() -> None:
    far_c = Stop(3, 35.13, 129.06)  # B~C 중간 지점은 A~B 중간 지점에서 1km 넘게 떨어짐
    lat, lon = midpoint(A, B)
    f, get = finder(lambda p: ok(item("near_ab", lat, lon)) if p["contentTypeId"] == 39 else ok())

    ab, bc, _lodging = asyncio.run(f.find([A, B, far_c]))

    assert [c.content_id for c in ab.candidates] == ["near_ab"] and ab.status == "ok"
    assert bc.candidates == [] and bc.status == "empty"
    assert sum(c["contentTypeId"] == 39 for c in get.calls) == 1


def test_reads_remaining_pages_up_to_the_limit() -> None:
    def reply(p: Mapping[str, Any]) -> dict:
        data = ok(item(f"p{p['pageNo']}", A.lat, A.lon))
        data["response"]["body"]["totalCount"] = PAGE_ROWS * (MAX_PAGES + 2)
        return data

    get = FakeGet(reply)
    places = asyncio.run(TourApiClient("test-key", get=get).busan(39))

    assert sorted(c["pageNo"] for c in get.calls) == list(range(1, MAX_PAGES + 1))
    assert len(places) == MAX_PAGES


def test_candidates_are_filtered_sorted_and_classified() -> None:
    lat, lon = midpoint(A, B)

    def reply(p: Mapping[str, Any]) -> dict:
        if p["contentTypeId"] == 32:
            return ok(item("h1", C.lat + 300 * M, C.lon, "AC010100", "호텔"))
        return ok(
            item("far", lat + 1200 * M, lon),  # 반경 밖
            item("bar", lat + 10 * M, lon, "FD040100"),  # 주점 제외
            item("cafe", lat + 200 * M, lon, "FD050100"),
            item("rice", lat + 50 * M, lon),
            item("rice", lat + 50 * M, lon),  # 중복
            {"contentid": "nocoord", "title": "좌표 없음"},
        )

    f, _ = finder(reply)
    seg, _seg2, lodging = asyncio.run(f.find([A, B, C]))

    assert [(c.content_id, c.category, c.distance_m) for c in seg.candidates] == [
        ("rice", "밥", 50),
        ("cafe", "카페", 200),
    ]
    assert seg.status == "ok" and seg.candidates[0].source_id == "DATA-13"
    assert [(c.name, c.category, c.facility_type) for c in lodging.candidates] == [
        ("호텔", "호텔", "lodging")
    ]


def test_one_failed_search_does_not_break_others() -> None:
    def reply(p: Mapping[str, Any]) -> Any:
        if p["contentTypeId"] == 32:
            return TourApiError("timeout")
        return ok(item("r", *midpoint(A, B)))

    f, _ = finder(reply)
    result = asyncio.run(f.find([A, B]))

    assert [(r.kind, r.status, r.error) for r in result] == [
        ("segment", "ok", None),
        ("lodging", "failed", "timeout"),
    ]


def test_missing_key_marks_all_failed_without_calling() -> None:
    get = FakeGet(lambda p: ok())
    result = asyncio.run(AmenityFinder(TourApiClient(None, get=get)).find([A, B]))

    assert {(r.status, r.error) for r in result} == {("failed", "no_key")} and get.calls == []


@pytest.mark.parametrize("n", [1, 11])
def test_stop_count_limits(n: int) -> None:
    f, _ = finder(lambda p: ok())
    with pytest.raises(ValueError):
        asyncio.run(f.find([A] * n))


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        ({"response": {"header": {"resultCode": "30"}}}, "result"),  # 키 미등록 등
        ({"OpenAPI_ServiceResponse": {}}, "format"),
        ("<xml/>", "format"),
    ],
)
def test_parse_errors(data: Any, kind: str) -> None:
    with pytest.raises(TourApiError) as e:
        _parse(data)
    assert e.value.kind == kind


def test_parse_single_item_dict() -> None:
    data = ok()
    data["response"]["body"]["items"] = {"item": item("one", 35.1, 129.0)}

    assert [p.content_id for p in _parse(data)] == ["one"]


def test_key_is_not_exposed() -> None:
    client = TourApiClient("secret-service-key")

    assert "secret-service-key" not in repr(client)
    assert is_excluded("FD040500") and not is_excluded("FD050100")

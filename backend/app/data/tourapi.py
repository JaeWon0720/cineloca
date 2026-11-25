"""한국관광공사 TourAPI 부산 음식점·숙박 목록 (COMP-11, DATA-13, 저장 정책 NO_STORE).

국문 관광정보 서비스(KorService2) areaBasedList2를 부산(법정동 시도 코드 26)으로 부른다.
응답은 요청 처리 중에만 쓰고 저장하지 않는다.
- 위치기반 조회(locationBasedList2)는 2026-11 기준 옛 지역코드(areacode)가 남은 항목만 찾는다.
  부산 음식점 511곳 중 48곳뿐이라(해운대 해변 1km: 실제 36곳 → 3곳) 지역 목록을 받아
  거리는 AmenityFinder가 직접 거른다. 부산 전체 음식점 1회 약 0.3초·300KB.
- 호출당 3초 제한(SDD 2.3). 실패는 TourApiError 하나로 바꾼다.
  키가 든 URL·응답 본문은 오류 메시지에 넣지 않는다.
- 공공데이터포털은 오류를 HTTP 200 + XML/JSON 본문으로 줄 때가 있어 resultCode까지 확인한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

URL = "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
TIMEOUT_S = 3.0
BUSAN_LDONG_REGN = "26"  # 법정동 시도 코드(부산광역시)
PAGE_ROWS = 1000  # 한 번에 받는 수. 2026-11 부산 음식점 511곳, 숙박 130곳
MAX_PAGES = 5  # 목록이 커져도 호출 수가 끝없이 늘지 않게


class TourApiError(Exception):
    """kind: timeout · http · format · result(공공데이터포털 resultCode 오류) · no_key"""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind} {detail}".strip())
        self.kind = kind


class JsonGet(Protocol):
    async def __call__(self, url: str, params: Mapping[str, Any], *, timeout_s: float) -> Any:
        """2xx면 응답 JSON. 실패는 TourApiError."""
        ...


class HttpxGet:
    async def __call__(self, url: str, params: Mapping[str, Any], *, timeout_s: float) -> Any:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                res = await client.get(url, params=dict(params))
        except httpx.TimeoutException:
            raise TourApiError("timeout") from None
        except httpx.HTTPError as e:  # 예외 문장에 키가 든 URL이 있어 종류만 남긴다
            raise TourApiError("http", type(e).__name__) from None
        if res.status_code >= 400:
            raise TourApiError("http", str(res.status_code))
        try:
            return res.json()
        except ValueError:
            raise TourApiError("format", "JSON 아님") from None  # 키 오류는 XML로 온다


@dataclass(frozen=True)
class Place:
    """TourAPI 항목에서 쓰는 값만."""

    content_id: str
    title: str
    address: str | None
    lat: float
    lon: float
    lcls_code: str | None  # lclsSystm3(없으면 lclsSystm2)


class TourApiClient:
    def __init__(self, service_key: str | None, get: JsonGet | None = None) -> None:
        self._key = service_key
        self._get = get or HttpxGet()

    def __repr__(self) -> str:  # 키가 디버그 출력에 찍히지 않게
        return "TourApiClient()"

    async def busan(self, content_type: int) -> list[Place]:
        """부산 전체 항목(contentTypeId 39 음식점·32 숙박).

        첫 쪽의 totalCount로 나머지 쪽을 동시에 받는다(MAX_PAGES까지).
        """
        if not self._key:
            raise TourApiError("no_key")
        first = await self._page(content_type, 1)
        places, total = _parse(first), _total(first)
        pages = min(MAX_PAGES, -(-total // PAGE_ROWS)) if total else 1
        if pages > 1:
            rest = await asyncio.gather(*(self._page(content_type, n) for n in range(2, pages + 1)))
            places += [p for data in rest for p in _parse(data)]
        return places

    async def _page(self, content_type: int, page: int) -> Any:
        return await self._get(
            URL,
            {
                "serviceKey": self._key,
                "MobileOS": "ETC",
                "MobileApp": "cineloca",
                "_type": "json",
                "lDongRegnCd": BUSAN_LDONG_REGN,
                "contentTypeId": content_type,
                "arrange": "A",
                "numOfRows": PAGE_ROWS,
                "pageNo": page,
            },
            timeout_s=TIMEOUT_S,
        )


def _parse(data: Any) -> list[Place]:
    try:
        body = data["response"]
        code = body["header"]["resultCode"]
    except (KeyError, TypeError):
        raise TourApiError("format", "response 없음") from None
    if code != "0000":
        raise TourApiError("result", str(code))
    items = (
        ((body.get("body") or {}).get("items") or {}) if isinstance(body.get("body"), dict) else {}
    )
    raw = items.get("item") if isinstance(items, dict) else None
    rows = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    places = []
    for r in rows:
        try:
            lat, lon = float(r["mapy"]), float(r["mapx"])
        except (KeyError, TypeError, ValueError):
            continue  # 좌표 없는 항목은 거리 계산을 못 해 뺀다
        places.append(
            Place(
                content_id=str(r.get("contentid") or ""),
                title=str(r.get("title") or "").strip(),
                address=(str(r.get("addr1") or "").strip() or None),
                lat=lat,
                lon=lon,
                lcls_code=(r.get("lclsSystm3") or r.get("lclsSystm2") or None),
            )
        )
    return [p for p in places if p.content_id and p.title]


def _total(data: Any) -> int:
    try:
        return int(data["response"]["body"]["totalCount"])
    except (KeyError, TypeError, ValueError):
        return 0

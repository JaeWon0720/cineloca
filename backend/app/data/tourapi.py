"""한국관광공사 TourAPI 위치기반 조회 (COMP-11, DATA-13, 저장 정책 NO_STORE).

국문 관광정보 서비스(KorService2) locationBasedList2. 응답은 요청 처리 중에만 쓰고 저장하지 않는다.
- 호출당 3초 제한(SDD 2.3). 실패는 TourApiError 하나로 바꾼다.
  키가 든 URL·응답 본문은 오류 메시지에 넣지 않는다.
- 공공데이터포털은 오류를 HTTP 200 + XML/JSON 본문으로 줄 때가 있어 resultCode까지 확인한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

URL = "https://apis.data.go.kr/B551011/KorService2/locationBasedList2"
TIMEOUT_S = 3.0
MAX_ROWS = 100  # 거리순(arrange=E)이라 가까운 100곳이면 충분하다


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
    dist_m: float | None  # TourAPI가 계산한 검색 중심에서의 거리
    lcls_code: str | None  # lclsSystm3(없으면 lclsSystm2)


class TourApiClient:
    def __init__(self, service_key: str | None, get: JsonGet | None = None) -> None:
        self._key = service_key
        self._get = get or HttpxGet()

    def __repr__(self) -> str:  # 키가 디버그 출력에 찍히지 않게
        return "TourApiClient()"

    async def nearby(self, lat: float, lon: float, radius_m: int, content_type: int) -> list[Place]:
        if not self._key:
            raise TourApiError("no_key")
        data = await self._get(
            URL,
            {
                "serviceKey": self._key,
                "MobileOS": "ETC",
                "MobileApp": "cineloca",
                "_type": "json",
                "mapX": lon,
                "mapY": lat,
                "radius": radius_m,
                "contentTypeId": content_type,
                "arrange": "E",
                "numOfRows": MAX_ROWS,
                "pageNo": 1,
            },
            timeout_s=TIMEOUT_S,
        )
        return _parse(data)


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
        dist = r.get("dist")
        places.append(
            Place(
                content_id=str(r.get("contentid") or ""),
                title=str(r.get("title") or "").strip(),
                address=(str(r.get("addr1") or "").strip() or None),
                lat=lat,
                lon=lon,
                dist_m=float(dist) if _number(dist) else None,
                lcls_code=(r.get("lclsSystm3") or r.get("lclsSystm2") or None),
            )
        )
    return [p for p in places if p.content_id and p.title]


def _number(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False

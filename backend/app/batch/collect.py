"""원본 수집 (FR-601). PoC p1_collect.py를 배치용 비동기 코드로 옮겼다.

- DATA-01 영화 속 부산정보, DATA-02 씨네맵: 촬영지 원본
  (작품명 opusNm, 장소명 plcNm, 개봉일·도로명주소)
- DATA-08 부산교통공사 역사 정보: 역 코드 scode, 역 이름 sname, 주소 saddr (호선·좌표 없음)
공공데이터포털은 오류를 200 본문으로 줄 때가 있어 본문을 검사한다.
키는 오류 메시지·로그에 남기지 않는다.
"""

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx

DATA_GO_KR = "https://apis.data.go.kr"
BUSAN_SOURCES = {
    "DATA-01": "/6260000/BfcMovieBusanService/getBfcMovieBusan",
    "DATA-02": "/6260000/CineMapService/getCineMap",
}
STATION_PATH = "/B551542/stationInfo/getStationInfo"
PAGE_ROWS = 1000
RETRY_WAITS = (2, 5, 10)


class CollectError(RuntimeError):
    pass


def find_items(obj: Any, marker: str) -> tuple[list[dict], int | None]:
    """응답 모양이 서비스마다 조금 달라도 marker 필드를 가진 항목과 totalCount를 찾는다."""
    items: list[dict] = []
    total = None
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "totalCount" in o:
                try:
                    total = int(o["totalCount"])
                except (TypeError, ValueError):
                    pass
            if marker in o:
                items.append(o)
                continue
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    items.reverse()  # 스택 순서를 원래 순서로
    return items, total


async def get_json(
    http: httpx.AsyncClient, url: str, params: dict, waits: Iterable[float] = RETRY_WAITS
) -> Any:
    """GET + 재시도(연결 오류·429·5xx). 키가 든 URL은 예외 메시지에 넣지 않는다."""
    waits = list(waits)
    for i in range(len(waits) + 1):
        try:
            r = await http.get(url, params=params, timeout=20)
            if r.status_code in (429, 500, 502, 503, 504) and i < len(waits):
                await asyncio.sleep(waits[i])
                continue
            if r.status_code != 200:
                raise CollectError(f"HTTP {r.status_code}")
            body = r.json()
            if isinstance(body, dict) and "OpenAPI_ServiceResponse" in body:
                head = body["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
                raise CollectError(
                    f"API 오류 {head.get('returnReasonCode')}: {head.get('returnAuthMsg')}"
                )
            return body
        except (httpx.TransportError, ValueError) as e:
            if i >= len(waits):
                raise CollectError(type(e).__name__) from None
            await asyncio.sleep(waits[i])
    raise CollectError("재시도 초과")


async def fetch_all(
    http: httpx.AsyncClient, path: str, key: str, marker: str, extra: dict | None = None
) -> list[dict]:
    out: list[dict] = []
    page, total = 1, None
    while True:
        params = {"serviceKey": key, "pageNo": page, "numOfRows": PAGE_ROWS, **(extra or {})}
        items, t = find_items(await get_json(http, DATA_GO_KR + path, params), marker)
        total = total or t
        out += items
        if not items or (total and len(out) >= total):
            return out
        page += 1


async def fetch_busan(http: httpx.AsyncClient, key: str) -> dict[str, list[dict]]:
    """촬영지 원본 2종. 반환: {'DATA-01': [...], 'DATA-02': [...]}"""
    return {
        sid: await fetch_all(http, path, key, "opusNm", {"resultType": "json"})
        for sid, path in BUSAN_SOURCES.items()
    }


async def fetch_stations(http: httpx.AsyncClient, key: str) -> list[dict]:
    return await fetch_all(http, STATION_PATH, key, "scode", {"_type": "json"})

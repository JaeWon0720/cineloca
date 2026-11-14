"""원본 수집 단위 테스트 (FR-601). 가짜 HTTP 응답만 쓴다(실제 공공데이터포털 호출 없음)."""

import asyncio

import httpx
import pytest

from app.batch import collect
from app.batch.collect import CollectError, fetch_all, find_items, get_json


def page(items: list[dict], total: int) -> dict:
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL_CODE"},
            "body": {"totalCount": str(total), "items": {"item": items}},
        }
    }


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_find_items_keeps_order_and_total() -> None:
    body = page([{"opusNm": "가", "plcNm": "1"}, {"opusNm": "나", "plcNm": "2"}], 2)
    items, total = find_items(body, "opusNm")
    assert [i["opusNm"] for i in items] == ["가", "나"] and total == 2


def test_fetch_all_follows_pages_until_total() -> None:
    rows = [{"opusNm": f"작품{i}", "plcNm": "곳"} for i in range(5)]
    seen: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        n, size = int(req.url.params["pageNo"]), int(req.url.params["numOfRows"])
        seen.append(n)
        return httpx.Response(200, json=page(rows[(n - 1) * size : n * size], len(rows)))

    collect_rows = 2
    old, collect.PAGE_ROWS = collect.PAGE_ROWS, collect_rows
    try:
        out = asyncio.run(fetch_all(client(handler), "/x", "KEY", "opusNm"))
    finally:
        collect.PAGE_ROWS = old
    assert [r["opusNm"] for r in out] == [r["opusNm"] for r in rows]
    assert seen == [1, 2, 3]


def test_api_error_in_200_body_raises_without_key() -> None:
    err = {
        "OpenAPI_ServiceResponse": {
            "cmmMsgHeader": {
                "returnReasonCode": "30",
                "returnAuthMsg": "SERVICE_KEY_IS_NOT_REGISTERED",
            }
        }
    }

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=err)

    with pytest.raises(CollectError) as e:
        asyncio.run(get_json(client(handler), "https://x/api", {"serviceKey": "SECRET"}, waits=()))
    assert "30" in str(e.value) and "SECRET" not in str(e.value)


def test_retries_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(collect.asyncio, "sleep", no_sleep)

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json=page([], 0))

    assert asyncio.run(get_json(client(handler), "https://x/api", {}, waits=(1, 1, 1)))
    assert calls["n"] == 3


def test_http_error_message_has_no_url() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(CollectError) as e:
        asyncio.run(get_json(client(handler), "https://x/api?serviceKey=SECRET", {}, waits=()))
    assert str(e.value) == "HTTP 401"

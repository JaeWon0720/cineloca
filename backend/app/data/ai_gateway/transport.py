"""공급자 HTTP 호출. 실패는 모두 ProviderError로 바꾼다(ADR-004).

공급자 구현은 JsonPost만 알고, 테스트는 가짜 JsonPost를 넣는다(실제 네트워크 없음).
오류 메시지에는 상태 코드만 남긴다. 응답 본문에는 입력 문장이 되돌아올 수 있고,
URL에는 키가 들 수 있어서 로그로 새지 않게 한다(NFR-005·009).
"""

from __future__ import annotations

import email.utils
import time
from collections.abc import Mapping
from typing import Any, Protocol

from app.data.ai_gateway.errors import RATE_LIMITED, TIMEOUT, UPSTREAM, ProviderError


class JsonPost(Protocol):
    async def __call__(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> Any:
        """2xx면 응답 JSON, 아니면 ProviderError."""
        ...


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    """Retry-After 헤더(초 또는 HTTP 날짜)를 초로 바꾼다. 읽을 수 없으면 None."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (time.time() if now is None else now))


def error_for_status(status: int, headers: Mapping[str, str]) -> ProviderError:
    """HTTP 오류 상태를 공급자 오류로. 429만 rate_limited, 나머지는 upstream."""
    if status == 429:
        retry = retry_after_seconds(headers.get("retry-after") or headers.get("Retry-After"))
        return ProviderError(RATE_LIMITED, "HTTP 429", retry)
    return ProviderError(UPSTREAM, f"HTTP {status}")


class HttpxPost:
    """httpx로 JSON을 POST한다. 요청마다 연결을 새로 연다(호출이 드물고 수명 관리가 단순).

    transport는 테스트에서 httpx.MockTransport를 넣을 때만 쓴다.
    """

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    async def __call__(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> Any:
        import httpx  # 공급자를 실제로 쓸 때만 필요하다

        try:
            async with httpx.AsyncClient(timeout=timeout_s, transport=self._transport) as client:
                res = await client.post(url, json=dict(payload), headers=dict(headers))
        except httpx.TimeoutException:
            raise ProviderError(TIMEOUT) from None
        except httpx.HTTPError as e:  # 연결 실패 등. 예외 문장에 URL이 들어 있어 종류만 남긴다
            raise ProviderError(UPSTREAM, type(e).__name__) from None
        if res.status_code >= 400:
            raise error_for_status(res.status_code, res.headers)
        try:
            return res.json()
        except ValueError:
            raise ProviderError(UPSTREAM, "JSON 아닌 응답") from None

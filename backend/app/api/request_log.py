"""요청 로그 (NFR-005, SDD 3.5 로그 파일·8장 로그 정책, ERR-016).

요청마다 JSON 한 줄을 logs/requests-YYYY-MM-DD.jsonl에 남기고, 7일이 지난 파일은 지운다.
남기는 것: 시각, 요청 ID, 메서드, 경로 틀(/api/locations/{location_id}), 상태, 응답 시간,
          AI 공급자별 호출 수, 오류 코드, F3·F4의 마스킹한 질의
남기지 않는 것: 실제 경로 값, 쿼리 문자열, 요청·응답 본문, 위치 좌표, IP, 헤더(토큰·쿠키)
모든 레코드는 쓰기 직전에 privacy.scrub()을 한 번 더 거친다.
"""

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.support import request_context
from app.support.privacy import scrub

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 시차로 충분하다
RETENTION_DAYS = 7
REQUEST_ID_HEADER = "X-Request-Id"
SCOPE_KEY = "cineloca.request_id"  # 500 처리기가 요청 ID를 꺼내 쓰는 자리

_FILE = re.compile(r"^requests-(\d{4}-\d{2}-\d{2})\.jsonl$")


class DailyJsonlWriter:
    """날짜별 JSON Lines 파일에 쓰고, 날짜가 바뀌면 보관 기간이 지난 파일을 지운다."""

    def __init__(
        self,
        folder: str | Path,
        retention_days: int = RETENTION_DAYS,
        now: Callable[[], datetime] = lambda: datetime.now(KST),
    ) -> None:
        self.folder = Path(folder)
        self.retention_days = retention_days
        self._now = now
        self._lock = threading.Lock()
        self._day: date | None = None

    def write(self, record: dict[str, Any]) -> None:
        today = self._now().date()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            if today != self._day:
                self.folder.mkdir(parents=True, exist_ok=True)
                self.prune(today)
                self._day = today
            with open(
                self.folder / f"requests-{today.isoformat()}.jsonl", "a", encoding="utf-8"
            ) as f:
                f.write(line)

    def prune(self, today: date) -> list[str]:
        """오늘을 포함해 retention_days일치만 남기고 지운다."""
        oldest_kept = today - timedelta(days=self.retention_days - 1)
        removed = []
        for path in self.folder.glob("requests-*.jsonl"):
            m = _FILE.match(path.name)
            if m and date.fromisoformat(m.group(1)) < oldest_kept:
                path.unlink(missing_ok=True)
                removed.append(path.name)
        return removed


def _route_template(scope: Scope) -> str | None:
    """라우터가 고른 경로 틀. 맞는 라우트가 없으면(404) 실제 경로 대신 None."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if template is None:
        return None
    # include_router(prefix=...)로 붙은 앞부분(/api)은 route.path에 없어서 실제 경로에서 되살린다
    params = {k: str(v) for k, v in (scope.get("path_params") or {}).items()}
    try:
        rendered = template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template
    path = scope.get("path", "")
    prefix = path[: -len(rendered)] if rendered and path.endswith(rendered) else ""
    return prefix + template


class RequestLogMiddleware:
    """요청마다 요청 ID를 붙이고 한 줄을 남긴다. writer가 없으면 ID만 붙인다."""

    def __init__(self, app: ASGIApp, writer: DailyJsonlWriter | None = None) -> None:
        self.app = app
        self.writer = writer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:16]  # 들어온 헤더 값은 쓰지 않는다(로그 위조 방지)
        scope[SCOPE_KEY] = request_id
        ctx, token = request_context.begin(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            status = 500
            ctx.error_code = ctx.error_code or "INTERNAL_ERROR"
            raise
        finally:
            request_context.end(token)
            if self.writer is not None:
                self._write(scope, ctx, status, started)

    def _write(
        self, scope: Scope, ctx: request_context.RequestContext, status: int, started: float
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(KST).isoformat(timespec="seconds"),
            "request_id": ctx.request_id,
            "method": scope.get("method"),
            "path": _route_template(scope),
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
        if ctx.ai_calls:
            record["ai_calls"] = dict(ctx.ai_calls)
        if ctx.error_code:
            record["error_code"] = ctx.error_code
        if ctx.masked_query is not None:
            record["query"] = ctx.masked_query
        try:
            self.writer.write(scrub(record))  # type: ignore[union-attr]
        except OSError:  # 로그를 못 써도 응답은 막지 않는다
            pass

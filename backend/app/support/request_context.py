"""요청 하나 동안의 기록 칸 (NFR-005 요청 로그).

요청 로그 미들웨어(app/api/request_log.py)가 요청마다 칸을 열고, 업무 서비스가 아래 함수로 채운다.
- AI를 부른 서비스(F1·F3·F4): note_ai_attempts(result.attempts 또는 AIUnavailable.attempts)
- 사용자 문장을 받은 서비스(F3·F4): note_masked_query(mask(text))

데이터 계층(AI Gateway)은 위 계층을 부르지 않으므로(ADR-002), 결과를 받은 서비스가 넘겨준다.
요청 밖(배치·테스트)에서 불러도 아무 일도 하지 않는다.
"""

from collections import Counter
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.support.privacy import MaskedText

# 실제로 공급자를 부르지 않은 시도(차단기 열림·한도 소진·마감 부족)는 호출 수에 넣지 않는다
_NOT_CALLED = frozenset({"circuit_open", "skipped_quota", "deadline"})


class _AttemptLike(Protocol):
    provider: str
    outcome: str


@dataclass
class RequestContext:
    request_id: str
    ai_calls: Counter[str] = field(default_factory=Counter)
    masked_query: str | None = None
    error_code: str | None = None


_current: ContextVar[RequestContext | None] = ContextVar("cineloca_request", default=None)


def begin(request_id: str) -> tuple[RequestContext, Any]:
    ctx = RequestContext(request_id=request_id)
    return ctx, _current.set(ctx)


def end(token: Any) -> None:
    _current.reset(token)


def current() -> RequestContext | None:
    return _current.get()


def note_ai_attempts(attempts: Iterable[_AttemptLike]) -> None:
    """AI 공급자별 호출 수를 더한다(NFR-005 'AI 공급자·호출 수')."""
    ctx = _current.get()
    if ctx is None:
        return
    for attempt in attempts:
        if attempt.outcome not in _NOT_CALLED:
            ctx.ai_calls[attempt.provider] += 1


def note_masked_query(masked: MaskedText) -> None:
    """F3·F4 질의를 마스킹한 형태로만 남긴다. 원문 문자열은 받지 않는다."""
    if not isinstance(masked, MaskedText):
        raise TypeError("요청 로그에는 마스킹한 문장(MaskedText)만 남길 수 있습니다 (NFR-003)")
    ctx = _current.get()
    if ctx is not None:
        ctx.masked_query = masked.text


def note_error(code: str) -> None:
    ctx = _current.get()
    if ctx is not None:
        ctx.error_code = code

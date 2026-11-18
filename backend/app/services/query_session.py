"""F3 질의 세션 (COMP-06 SessionStore, FR-308, AI-REQ-15).

- 세션마다 최근 3턴의 해석 조건을 서버 메모리에 30분 둔다(SRS 데이터 표 '질의 세션').
  DB에는 남기지 않는다(저장 정책: 세션은 저장 금지).
- 후속 질의('그중 해운대구만')는 직전 조건에 새 조건을 더한다. 새 질의가 말한 항목은 새 값,
  말하지 않은 항목은 이전 값을 쓴다. '새 대화'(reset)로 지운다.
- AI를 부르는 질의는 세션당 1분에 5회까지(AI-REQ-15).
API 서버는 워커 1개(Dockerfile)라 프로세스 메모리 하나로 충분하다.
"""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.data.ai_gateway.tasks import QueryConditions

SESSION_TTL_S = 30 * 60
MAX_TURNS = 3
RATE_LIMIT = 5
RATE_WINDOW_S = 60.0
MAX_SESSIONS = 10_000  # 메모리 상한. 넘으면 가장 오래 안 쓴 세션부터 지운다

_LIST_FIELDS = ("work_titles", "persons", "genres", "districts")
_VALUE_FIELDS = ("release_year_from", "release_year_to", "anchor", "travel")


def merge_conditions(prev: QueryConditions | None, new: QueryConditions) -> QueryConditions:
    """후속 질의 조건 합치기(FR-308). 새 질의가 말한 항목만 바꾼다."""
    if prev is None:
        return new
    merged: dict[str, Any] = {
        "out_of_scope": new.out_of_scope,
        "result_type": new.result_type,
    }
    for name in _LIST_FIELDS:
        merged[name] = getattr(new, name) or getattr(prev, name)
    for name in _VALUE_FIELDS:
        value = getattr(new, name)
        merged[name] = value if value is not None else getattr(prev, name)
    if new.release_year_from is not None or new.release_year_to is not None:
        # 연도는 범위 한 쌍으로 다룬다('2010년대' 뒤 '2015년 이후'면 이전 끝값을 남기지 않음)
        merged["release_year_from"] = new.release_year_from
        merged["release_year_to"] = new.release_year_to
    return QueryConditions.model_validate(merged)


@dataclass
class Session:
    id: str
    touched: float
    turns: deque[QueryConditions] = field(default_factory=lambda: deque(maxlen=MAX_TURNS))
    calls: deque[float] = field(default_factory=deque)
    pending: Any = None  # 고르기 대기(FR-303 4a)·칩 다시 검색(FR-312)에 쓰는 마지막 상태

    @property
    def last(self) -> QueryConditions | None:
        return self.turns[-1] if self.turns else None


class SessionStore:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str | None) -> Session:
        """있으면 그 세션, 없거나 만료됐으면 새 세션(새 ID)."""
        now = self._clock()
        self._expire(now)
        session = self._sessions.get(session_id or "")
        if session is None:
            session = Session(id=secrets.token_urlsafe(16), touched=now)
            self._sessions[session.id] = session
        session.touched = now
        return session

    def reset(self, session_id: str | None) -> None:
        """'새 대화'(API-015): 맥락을 지운다."""
        self._sessions.pop(session_id or "", None)

    def allow_ai_call(self, session: Session) -> float | None:
        """1분에 5회 안이면 기록하고 None, 넘으면 다시 시도할 수 있을 때까지 남은 초."""
        now = self._clock()
        while session.calls and now - session.calls[0] >= RATE_WINDOW_S:
            session.calls.popleft()
        if len(session.calls) >= RATE_LIMIT:
            return max(1.0, RATE_WINDOW_S - (now - session.calls[0]))
        session.calls.append(now)
        return None

    def __len__(self) -> int:
        return len(self._sessions)

    def _expire(self, now: float) -> None:
        stale = [k for k, s in self._sessions.items() if now - s.touched >= SESSION_TTL_S]
        for key in stale:
            del self._sessions[key]
        while len(self._sessions) >= MAX_SESSIONS:
            oldest = min(self._sessions.values(), key=lambda s: s.touched)
            del self._sessions[oldest.id]

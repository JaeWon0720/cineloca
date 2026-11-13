"""AI Gateway 오류."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.data.ai_gateway.contracts import Attempt

# 공급자 실패 종류
TIMEOUT = "timeout"
RATE_LIMITED = "rate_limited"
UPSTREAM = "upstream"


class ProviderError(Exception):
    """공급자 한 번 호출의 실패. 공급자 구현은 HTTP 오류·시간 초과를 이 예외로 바꿔서 던진다."""

    def __init__(self, kind: str, message: str = "", retry_after_s: float | None = None) -> None:
        super().__init__(message or kind)
        self.kind = kind
        self.retry_after_s = retry_after_s


class AIUnavailable(Exception):
    """모든 공급자가 실패해 강등해야 함. 호출한 업무 서비스는 AI 없는 기본 결과를 돌려준다.

    error_code: AI_UNAVAILABLE(공급자 오류·차단기·한도), AI_SCHEMA(출력 형식 위반),
                AI_DEADLINE(마감 시간 부족)
    attempts: 공급자별 시도 기록(로그용, 사용자 입력은 들어 있지 않음)
    """

    def __init__(self, task: str, error_code: str, attempts: tuple[Attempt, ...]) -> None:
        super().__init__(f"{task}: {error_code}")
        self.task = task
        self.error_code = error_code
        self.attempts = attempts

"""공급자 차단기 (AI-REQ-02: 같은 공급자가 연속 5회 실패하면 60초 동안 호출하지 않는다)."""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        open_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._open_seconds = open_seconds
        self._clock = clock
        self._failures = 0
        self._open_until: float | None = None

    @property
    def is_open(self) -> bool:
        return self._open_until is not None and self._clock() < self._open_until

    def allow(self) -> bool:
        """호출해도 되면 True. 60초가 지나면 한 번 다시 시도해 본다(실패하면 바로 다시 열림)."""
        return not self.is_open

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = self._clock() + self._open_seconds

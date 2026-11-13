"""동기화 건수·실패 목록과 리포트 (FR-613, ERR-013).

배치는 단계를 마칠 때마다 SyncCounts를 채우고, 실패는 failure()로 만들어 모은다.
리포트는 sync_run 한 건을 관리자 화면(UI-10)에 보여 줄 형태로 바꾼 것이다.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.support.privacy import mask

RunStatus = Literal["requested", "running", "succeeded", "failed", "guarded"]
GUARD_STEP = "count_guard"  # 건수 가드 위반 사유는 이 단계 이름으로 실패 목록에 남긴다
MAX_FAILURES = 200  # 한 번에 남기는 실패 수 상한(나머지는 개수만)
_MAX_REASON = 300

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRET_PARAM = re.compile(
    r"\b([A-Za-z_]*(?:key|token|secret|password|passwd))\s*[=:]\s*\S+", re.IGNORECASE
)


class MatchCounts(BaseModel):
    """작품 매칭 결과(BR-11). 세 값의 합이 작품 수다."""

    matched: int = Field(default=0, ge=0, description="KMDb에 연결됨(자동·검수 확정)")
    review: int = Field(default=0, ge=0, description="검수 대기")
    unmatched: int = Field(default=0, ge=0, description="미매칭")


class GeoCounts(BaseModel):
    """촬영지 좌표 확보 결과(FR-605). 두 값의 합이 촬영지 수다."""

    located: int = Field(default=0, ge=0, description="좌표 있음(자동·수동)")
    failed: int = Field(default=0, ge=0, description="좌표 없음")


class SyncCounts(BaseModel):
    """동기화 한 번의 단계별 건수(FR-613). sync_run.counts에 그대로 저장한다."""

    fetched: dict[str, int] = Field(
        default_factory=dict, description="데이터셋별 수집 레코드 수(예: DATA-01)"
    )
    works: int = Field(default=0, ge=0, description="병합 후 작품 수")
    locations: int = Field(default=0, ge=0, description="병합 후 촬영지 수")
    match: MatchCounts = Field(default_factory=MatchCounts)
    geo: GeoCounts = Field(default_factory=GeoCounts)


class Failure(BaseModel):
    """실패 한 건. 주소(URL)·키는 남기지 않는다(ERR-013)."""

    dataset: str | None = None
    step: str
    reason: str


def clean_reason(text: str) -> str:
    """실패 사유에서 URL·키 값·개인정보를 지우고 길이를 줄인다."""
    text = _URL.sub("[URL 제거]", text)
    text = _SECRET_PARAM.sub(lambda m: f"{m.group(1)}=[제거됨]", text)
    text = mask(" ".join(text.split())).text
    return text if len(text) <= _MAX_REASON else text[: _MAX_REASON - 1] + "…"


def failure(step: str, reason: str | BaseException, dataset: str | None = None) -> Failure:
    """실패 기록을 만든다. 예외를 넘기면 예외 종류와 메시지를 정제해서 쓴다."""
    if isinstance(reason, BaseException):
        detail = str(reason)
        reason = type(reason).__name__ + (f": {detail}" if detail else "")
    return Failure(dataset=dataset, step=step, reason=clean_reason(reason))


def limit_failures(failures: list[Failure]) -> list[Failure]:
    """실패가 너무 많으면 앞의 MAX_FAILURES건만 남기고 나머지는 개수로 알린다."""
    if len(failures) <= MAX_FAILURES:
        return failures
    rest = len(failures) - (MAX_FAILURES - 1)
    return failures[: MAX_FAILURES - 1] + [
        Failure(step="report", reason=f"실패 {rest}건을 더 생략했습니다")
    ]


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


class SyncRates(BaseModel):
    """비율(0~1). 분모가 0이면 null."""

    match_rate: float | None = Field(description="KMDb 연결 작품 / 작품")
    review_rate: float | None = Field(description="검수 대기 작품 / 작품")
    geo_rate: float | None = Field(description="좌표 있는 촬영지 / 촬영지")


def rates(counts: SyncCounts) -> SyncRates:
    return SyncRates(
        match_rate=_ratio(counts.match.matched, counts.works),
        review_rate=_ratio(counts.match.review, counts.works),
        geo_rate=_ratio(counts.geo.located, counts.locations),
    )


class SyncReport(BaseModel):
    """동기화 리포트 한 건(FR-613). 관리자 조회 API(API-021)가 이 형식으로 돌려준다."""

    run_id: int
    trigger: Literal["schedule", "manual"]
    status: RunStatus
    started_at: datetime | None
    finished_at: datetime | None
    counts: SyncCounts | None
    rates: SyncRates | None
    prev_counts: SyncCounts | None
    guard: list[str] = Field(description="건수 가드 위반 사유(FR-612). 비어 있으면 통과")
    failures: list[Failure] = Field(description="가드 사유를 뺀 실패 목록")


def build_report(
    run_id: int,
    trigger: Literal["schedule", "manual"],
    status: RunStatus,
    started_at: datetime | None,
    finished_at: datetime | None,
    counts: SyncCounts | None,
    prev_counts: SyncCounts | None,
    failures: list[Failure],
) -> SyncReport:
    return SyncReport(
        run_id=run_id,
        trigger=trigger,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        counts=counts,
        rates=rates(counts) if counts else None,
        prev_counts=prev_counts,
        guard=[f.reason for f in failures if f.step == GUARD_STEP],
        failures=[f for f in failures if f.step != GUARD_STEP],
    )

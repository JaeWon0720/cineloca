"""관리자 API 스키마: API-020 관리자 로그인(FR-615), API-021 동기화 요청·리포트."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.batch.report import SyncReport
from app.services.admin_auth import MAX_PASSWORD_CHARS


class AdminLoginRequest(BaseModel):
    password: str = Field(
        min_length=1, max_length=MAX_PASSWORD_CHARS, description="관리자 비밀번호"
    )


class AdminSessionResponse(BaseModel):
    """로그인 상태. 토큰은 쿠키로만 주고 본문에는 넣지 않는다."""

    authenticated: bool = True
    expires_at: datetime = Field(description="세션 만료 시각(발급 2시간 뒤)")


# ── API-021 동기화 요청·리포트 (FR-612·613·616, NFR-005) ─────────
class SyncRequestResponse(BaseModel):
    run_id: int
    created: bool = Field(description="false면 이미 대기·실행 중인 동기화가 있어 그 id를 돌려줌")
    message: str


class ActiveSync(BaseModel):
    run_id: int
    trigger: Literal["schedule", "manual"]
    status: Literal["requested", "running"]
    requested_at: datetime
    started_at: datetime | None


class UsageItem(BaseModel):
    key: str = Field(description="gemini · DATA-01 등")
    count: int
    limit: int | None
    ratio: float | None = Field(description="count / limit (0~1). 한도가 없으면 null")
    level: Literal["ok", "warning", "exhausted"] = Field(description="70% 이상 warning")


class SyncSchedule(BaseModel):
    interval_days: int = Field(description="정기 동기화 주기(일). 0이면 끔(FR-616)")
    last_started_at: datetime | None
    next_due_at: datetime | None = Field(description="다음 정기 실행 예정. 지난 시각이면 곧 시작")


class SyncOverviewResponse(BaseModel):
    """UI-10 동기화 리포트 화면 한 번에 필요한 값."""

    active: ActiveSync | None = Field(description="대기·실행 중인 동기화(없으면 null)")
    alerts: list[str] = Field(description="건수 가드·실패·호출 한도 경고(FR-612, NFR-018)")
    reports: list[SyncReport] = Field(description="최근 10회 리포트, 최신 먼저(FR-613)")
    usage_day: date = Field(description="호출 수 기준일(한국 시간)")
    usage: list[UsageItem] = Field(description="오늘 외부 호출 수(NFR-005)")
    schedule: SyncSchedule

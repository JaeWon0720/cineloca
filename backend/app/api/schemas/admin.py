"""관리자 API 스키마: API-020 관리자 로그인 (FR-615)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.admin_auth import MAX_PASSWORD_CHARS


class AdminLoginRequest(BaseModel):
    password: str = Field(
        min_length=1, max_length=MAX_PASSWORD_CHARS, description="관리자 비밀번호"
    )


class AdminSessionResponse(BaseModel):
    """로그인 상태. 토큰은 쿠키로만 주고 본문에는 넣지 않는다."""

    authenticated: bool = True
    expires_at: datetime = Field(description="세션 만료 시각(발급 2시간 뒤)")

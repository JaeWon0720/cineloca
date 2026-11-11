"""API-025 응답 스키마 (v1: DB 점검 추가, AI 공급자 점검은 AI Gateway 구현 때 추가)."""

from typing import Literal

from pydantic import BaseModel


class HealthChecks(BaseModel):
    db: Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: HealthChecks

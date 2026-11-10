"""API-025 상태 확인 (NFR-002, NFR-005, NFR-020).

DB가 응답하면 200 {"status": "ok"}, 응답하지 않으면 503 {"status": "degraded"}.
Docker 헬스체크와 시연 전 점검에 쓴다.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app import __version__
from app.api.schemas.health import HealthChecks, HealthResponse
from app.services import health as health_service

router = APIRouter(tags=["health"])

DatabaseChecker = Callable[[], Awaitable[bool]]


def get_database_checker() -> DatabaseChecker:
    return health_service.check_database


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "DB 등 의존 서비스 이상"}},
)
async def get_health(
    response: Response,
    check_database: Annotated[DatabaseChecker, Depends(get_database_checker)],
) -> HealthResponse:
    db_ok = await check_database()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        checks=HealthChecks(db="ok" if db_ok else "error"),
    )

"""API-025 상태 확인 (NFR-002, NFR-005, NFR-020).

v0: 서버 프로세스가 응답하는지만 확인한다. DB·AI 공급자 상태 점검은 Phase 3에서 추가한다.
"""

from fastapi import APIRouter

from app import __version__
from app.api.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)

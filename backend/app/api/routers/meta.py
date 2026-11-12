"""API-007 선택 목록 (FR-202, FR-205, FR-310).

기준 위치 기본값·반경·구·군 중심점은 고정값이고, 역·장르·연도 범위는 지금은 예시 값이다(#27).
"""

from fastapi import APIRouter

from app.api import examples
from app.api.schemas.meta import MetaResponse

router = APIRouter(tags=["공통"])


@router.get("/meta", response_model=MetaResponse, summary="API-007 선택 목록")
async def get_meta() -> MetaResponse:
    return examples.meta()

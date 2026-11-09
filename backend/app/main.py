"""API 서버 진입점 (COMP-02, ADR-001).

실행: backend 폴더에서 `python -m uvicorn app.main:app --reload`
"""

from fastapi import FastAPI

from app import __version__
from app.api.routers import health


def create_app() -> FastAPI:
    app = FastAPI(
        title="CineLoca API",
        version=__version__,
        description="영화·드라마 속 부산 촬영지 AI 안내 서비스 API",
    )
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()

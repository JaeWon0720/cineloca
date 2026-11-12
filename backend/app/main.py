"""API 서버 진입점 (COMP-02, ADR-001).

실행: backend 폴더에서 `python -m uvicorn app.main:app --reload`
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import install_error_handlers
from app.api.routers import health, locations, meta
from app.data import db
from app.support.privacy import install_privacy_log_filter


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    install_privacy_log_filter()  # 로그에 개인정보가 남지 않게 정제한다(NFR-003·005)
    yield
    await db.close_pool()  # 종료할 때 DB 연결을 정리한다


def create_app() -> FastAPI:
    app = FastAPI(
        title="CineLoca API",
        version=__version__,
        description="영화·드라마 속 부산 촬영지 AI 안내 서비스 API",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(locations.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    return app


app = create_app()

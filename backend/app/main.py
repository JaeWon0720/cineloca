"""API 서버 진입점 (COMP-02, ADR-001).

실행: backend 폴더에서 `python -m uvicorn app.main:app --reload`
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import install_error_handlers
from app.api.request_log import DailyJsonlWriter, RequestLogMiddleware
from app.api.routers import health, locations, meta
from app.core.config import Settings, get_settings
from app.data import db
from app.support.privacy import install_privacy_log_filter


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    install_privacy_log_filter()  # 로그에 개인정보가 남지 않게 정제한다(NFR-003·005)
    _set_app_log_level(get_settings().log_level)
    yield
    await db.close_pool()  # 종료할 때 DB 연결을 정리한다


def _set_app_log_level(level: str) -> None:
    """app.* 로거 수준(기본 INFO). LOG_LEVEL=WARNING이면 경고 이상만 콘솔에 나온다."""
    app_logger = logging.getLogger("app")
    if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
        app_logger.setLevel(level)
    else:
        app_logger.setLevel(logging.INFO)
        app_logger.warning("LOG_LEVEL 값을 알 수 없어 INFO로 둡니다")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="CineLoca API",
        version=__version__,
        description="영화·드라마 속 부산 촬영지 AI 안내 서비스 API",
        lifespan=lifespan,
    )
    install_error_handlers(app)
    # 요청 로그(NFR-005): LOG_DIR이 있으면 파일로, 없으면 요청 ID만 붙인다
    writer = DailyJsonlWriter(settings.log_dir) if settings.log_dir else None
    app.add_middleware(RequestLogMiddleware, writer=writer)
    app.include_router(health.router, prefix="/api")
    app.include_router(locations.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    return app


app = create_app()

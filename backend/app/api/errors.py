"""공통 오류 응답 (SDD 3.2: {error: {code, message, retry_after}}).

라우터는 ApiError를 던지고, 요청 형식 오류(422)와 일반 HTTP 오류도 같은 형식으로 바꿔 돌려준다.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.request_log import REQUEST_ID_HEADER, SCOPE_KEY
from app.api.schemas.common import ErrorBody, ErrorResponse
from app.support import request_context

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, retry_after: int | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after


def error_response(
    status_code: int, code: str, message: str, retry_after: int | None = None
) -> JSONResponse:
    request_context.note_error(code)  # 요청 로그의 오류 코드(NFR-005)
    body = ErrorResponse(error=ErrorBody(code=code, message=message, retry_after=retry_after))
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


# OpenAPI 문서에 오류 형식을 보여 주기 위한 공통 responses
ERROR_RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "대상 없음"},
    422: {"model": ErrorResponse, "description": "요청 형식 오류(INVALID_REQUEST)"},
}

_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 429: "RATE_LIMITED"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        # 입력값(좌표 등)이 응답·로그에 남지 않게 어느 칸이 틀렸는지만 알려 준다(NFR-003)
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()})
        message = "요청 형식이 올바르지 않습니다: " + ", ".join(fields)
        return error_response(422, "INVALID_REQUEST", message)

    @app.exception_handler(Exception)
    async def _unhandled(req: Request, exc: Exception) -> JSONResponse:
        # ERR-016: 500과 요청 ID. 스택 추적은 로그 필터가 개인정보를 가린 뒤 남긴다
        request_id = req.scope.get(SCOPE_KEY, "-")
        logger.error("처리하지 못한 오류 request_id=%s", request_id, exc_info=exc)
        response = error_response(
            500, "INTERNAL_ERROR", f"서버 오류가 났습니다. 요청 ID: {request_id}"
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return error_response(exc.status_code, code, str(exc.detail))

"""One error envelope for every failure: {error: {code, message, details[]}, request_id}."""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = structlog.get_logger()

_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
}


def envelope(
    request: Request, status: int, code: str, message: str, details: list[Any] | None = None
) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details or []}, "request_id": rid},
        status_code=status,
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        return envelope(request, exc.status_code, _CODES.get(exc.status_code, "error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return envelope(request, 422, "validation_error", "request is invalid", list(exc.errors()))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled", path=request.url.path)
        return envelope(request, 500, "internal", "internal error")

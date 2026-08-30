"""One error shape for every failure `[EH-10]`.

    {"error": {"code", "message", "details": [...], "correlation_id"}}

Including FastAPI's own validation errors and any unhandled exception, so a
client never has to parse two formats, and every error carries the id that finds
it in the logs. Detail beyond the message goes to the logs and the trace, never
to the caller.
"""

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from texting_agent.deps import APIError

log = structlog.get_logger()


def envelope(request: Request, status_code: int, code: str, message: str,
             details: list[dict] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {
            "code": code,
            "message": message,
            "details": details or [],
            "correlation_id": getattr(request.state, "request_id", None),
        }},
    )


def register(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError) -> JSONResponse:
        return envelope(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
                 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 429: "RATE_LIMITED"}
        return envelope(request, exc.status_code,
                        codes.get(exc.status_code, "ERROR"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request,
                                exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in error["loc"][1:]),
             "problem": error["msg"]}
            for error in exc.errors()
        ]
        return envelope(request, 422, "INVALID_REQUEST",
                        "The request body or parameters were not valid.", details)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The exception itself goes to the logs with the same correlation id the
        # caller receives, so a support conversation starts from one identifier.
        log.exception("request.failed", path=request.url.path)
        return envelope(request, 500, "INTERNAL_ERROR",
                        "The request could not be completed.")

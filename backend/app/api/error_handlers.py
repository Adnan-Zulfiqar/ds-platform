"""Global exception handlers.

The single place where an exception becomes an HTTP response. Every failure —
domain error, validation failure, database error, unhandled bug — leaves through
here and produces the same :class:`ErrorResponse` shape, so a client needs one
error path rather than one per endpoint.

Two rules govern what reaches the client:

1. **Never leak internals.** Driver messages, tracebacks and SQL contain table
   names, column names and sometimes data values. Those go to the log, and the
   client receives a generic message plus the request id that locates the log
   line.
2. **Always include the request id.** It converts "the app is broken" into a
   single indexed log query.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import MissingTenantContextError, get_request_id
from app.core.exceptions import AppError, RateLimitExceededError
from app.core.logging import get_logger
from app.schemas.common import ErrorDetail, ErrorResponse

logger = get_logger(__name__)


def _render(
    status_code: int,
    payload: ErrorResponse,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", by_alias=True),
        headers=headers,
    )


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """Handle expected domain errors.

    These are anticipated outcomes, not faults, so they are logged at warning
    (4xx) or error (5xx) without a traceback.
    """
    details = [ErrorDetail(field=None, message=str(v), type=k) for k, v in exc.details.items()]
    payload = ErrorResponse(
        code=exc.code,
        message=exc.message,
        details=details,
        request_id=get_request_id(),
    )

    log = logger.error if exc.status_code >= 500 else logger.warning
    log("application_error", code=exc.code, status_code=exc.status_code, message=exc.message)

    headers: dict[str, str] | None = None
    if isinstance(exc, RateLimitExceededError) and exc.retry_after_seconds:
        headers = {"Retry-After": str(exc.retry_after_seconds)}

    return _render(exc.status_code, payload, headers)


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle request schema validation failures.

    Pydantic's raw error list is reshaped into flat field/message pairs. The raw
    form includes an ``input`` key echoing the submitted value, which would put
    a rejected password straight into the response body and the logs.
    """
    details = [
        ErrorDetail(
            field=".".join(str(part) for part in error["loc"][1:]) or None,
            message=error["msg"],
            type=error["type"],
        )
        for error in exc.errors()
    ]
    payload = ErrorResponse(
        code="validation_error",
        message="The request payload failed validation.",
        details=details,
        request_id=get_request_id(),
    )
    logger.warning("request_validation_failed", error_count=len(details))
    return _render(status.HTTP_422_UNPROCESSABLE_ENTITY, payload)


async def pydantic_error_handler(_request: Request, exc: PydanticValidationError) -> JSONResponse:
    """Handle a validation error raised outside request parsing.

    Reaching here means a response model or an internal construction failed
    validation — a server-side bug. The client is told nothing beyond a generic
    500 because the detail describes our own data shapes.
    """
    logger.exception("internal_validation_error", error_count=len(exc.errors()))
    payload = ErrorResponse(
        code="internal_error",
        message="An unexpected error occurred.",
        request_id=get_request_id(),
    )
    return _render(status.HTTP_500_INTERNAL_SERVER_ERROR, payload)


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle framework-raised HTTP exceptions, e.g. 404 for an unknown route.

    Normalised into the standard envelope so that a mistyped URL and a failed
    business rule are parsed identically by the client.
    """
    code = {
        401: "authentication_required",
        403: "permission_denied",
        404: "not_found",
        405: "method_not_allowed",
    }.get(exc.status_code, "http_error")

    payload = ErrorResponse(
        code=code,
        message=str(exc.detail),
        request_id=get_request_id(),
    )
    return _render(exc.status_code, payload, getattr(exc, "headers", None))


async def tenant_context_error_handler(
    _request: Request, exc: MissingTenantContextError
) -> JSONResponse:
    """Handle a tenant-scoped operation attempted with no tenant bound.

    Always a bug: a code path reached a scoped repository without passing
    through tenant resolution. Logged at error with a traceback because it needs
    fixing, and reported as a generic 500 because the client did nothing wrong.
    """
    logger.exception("missing_tenant_context", error=str(exc))
    payload = ErrorResponse(
        code="internal_error",
        message="An unexpected error occurred.",
        request_id=get_request_id(),
    )
    return _render(status.HTTP_500_INTERNAL_SERVER_ERROR, payload)


async def database_error_handler(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Handle a database error that no repository translated.

    Driver messages routinely contain table names, column names and fragments of
    the offending row, so nothing from ``exc`` reaches the response.
    """
    logger.exception("unhandled_database_error", error_type=type(exc).__name__)
    payload = ErrorResponse(
        code="database_error",
        message="A database error occurred.",
        request_id=get_request_id(),
    )
    return _render(status.HTTP_503_SERVICE_UNAVAILABLE, payload)


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler.

    Anything reaching here is an unanticipated bug. The traceback goes to the
    log; the client gets a generic message and the request id.
    """
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    payload = ErrorResponse(
        code="internal_error",
        message="An unexpected error occurred.",
        request_id=get_request_id(),
    )
    return _render(status.HTTP_500_INTERNAL_SERVER_ERROR, payload)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach every handler.

    Order is irrelevant — Starlette dispatches on exact exception type, walking
    the MRO — but the generic ``Exception`` handler must be present or an
    unhandled error returns Starlette's HTML error page instead of JSON.
    """
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PydanticValidationError, pydantic_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(MissingTenantContextError, tenant_context_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(SQLAlchemyError, database_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)


__all__ = ["register_exception_handlers"]

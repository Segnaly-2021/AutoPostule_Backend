"""
Global exception handlers for the FastAPI application.

These handlers ensure that no raw exception, SQL error, or internal detail
ever reaches the frontend. All unexpected errors are:
  1. Logged server-side with full traceback for debugging
  2. Assigned a correlation ID so users can reference the exact error
  3. Returned to the client as a sanitized, generic message

Layered design:
  - HTTPException handler   → known, intentional errors (401, 404, etc.)
  - Validation handler      → Pydantic request validation failures (422)
  - Catch-all handler       → everything else, fully sanitized
"""

import uuid
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """
    Handler for intentionally raised HTTPExceptions.
    These are already user-safe (you wrote the message yourself), so we
    pass them through with a consistent structure.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": f"HTTP_{exc.status_code}"
        }
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    Handler for Pydantic request validation failures.
    Returns field-level details but no internal specifics.
    """
    # Sanitize: only expose field name and user-safe error message
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(loc) for loc in error.get("loc", []) if loc != "body"),
            "message": error.get("msg", "Invalid input")
        })

    logger.warning(f"Validation error on {request.url.path}: {errors}")

    return JSONResponse(
        status_code=422,
        content={
            "error": "Invalid request data",
            "code": "VALIDATION_ERROR",
            "details": errors
        }
    )


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler for any exception not caught by more specific handlers.

    This is the critical safety net that ensures raw SQL errors, database
    driver exceptions (like asyncpg.DuplicatePreparedStatementError),
    serialization errors, and any other unexpected exception NEVER reach
    the frontend with their raw message.

    The full error is logged server-side with a correlation ID so developers
    can locate the exact error in logs when a user reports an issue.
    """
    # Generate a short, user-friendly correlation ID
    error_id = uuid.uuid4().hex[:12]

    # Log the full error server-side with all context
    logger.error(
        f"Unhandled exception [error_id={error_id}] "
        f"on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {str(exc)}\n"
        f"{traceback.format_exc()}"
    )

    # Return a sanitized response — no class names, no messages, no tracebacks
    return JSONResponse(
        status_code=500,
        content={
            "error": "An unexpected error occurred. Please try again later.",
            "code": "INTERNAL_ERROR",
            "error_id": error_id
        }
    )


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register all global exception handlers on the FastAPI app.

    Call this from your app factory AFTER creating the FastAPI instance
    but BEFORE including routers.
    """
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    logger.info("✅ Global exception handlers registered")
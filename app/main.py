
"""
PulseFleet — Predictive Exception & Delay Alert System

Day 1: Foundation and environment
Day 4: Create workflow — routers + clean error handling
Day 7: Authentication — auth router + protected-route error handling
Day 9: Search & filters — consistent validation error contract
"""

from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.exceptions import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.routers import auth, drivers, vehicles, shipments


app = FastAPI(
    title="PulseFleet",
    description="Predictive exception & delay alert system for fleet/shipment operations.",
    version=__version__,
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
# Every API error follows the common contract:
#
# {
#     "detail": "...",
#     "error_code": "..."
# }
#
# The API does not expose stack traces, SQL details, or internal exceptions.
# ---------------------------------------------------------------------------


@app.exception_handler(NotFoundError)
async def not_found_handler(
    request: Request,
    exc: NotFoundError,
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "detail": exc.message,
            "error_code": exc.error_code,
        },
    )


@app.exception_handler(ConflictError)
async def conflict_handler(
    request: Request,
    exc: ConflictError,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": exc.message,
            "error_code": exc.error_code,
        },
    )


@app.exception_handler(UnauthorizedError)
async def unauthorized_handler(
    request: Request,
    exc: UnauthorizedError,
) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "detail": exc.message,
            "error_code": exc.error_code,
        },
        headers={
            "WWW-Authenticate": "Bearer",
        },
    )


@app.exception_handler(ValidationError)
async def validation_domain_handler(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    """
    Handle application-level validation errors.

    Example:
        min_weight_kg > max_weight_kg

    Returns the same error contract used by the rest of the API.
    """
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.message,
            "error_code": exc.error_code,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    Handle FastAPI/Pydantic request validation errors.

    Converts FastAPI's default validation response into the project's
    consistent {detail, error_code} format.
    """
    errors = exc.errors()

    if errors:
        first = errors[0]

        field = ".".join(
            str(part)
            for part in first.get("loc", [])
            if part != "body"
        )

        message = first.get("msg", "Invalid request.")

        if field:
            message = f"{field}: {message}"
    else:
        message = "Invalid request."

    return JSONResponse(
        status_code=422,
        content={
            "detail": message,
            "error_code": "validation_error",
        },
    )


# ---------------------------------------------------------------------------
# System endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["system"])
def health_check() -> dict:
    """
    Basic liveness/readiness probe.

    Returns service status, current UTC time, and application version.
    """
    return {
        "status": "ok",
        "service": "PulseFleet",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "message": "PulseFleet API is running. See /docs for API documentation."
    }


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(drivers.router)
app.include_router(vehicles.router)
app.include_router(shipments.router)
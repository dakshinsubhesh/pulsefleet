"""
PulseFleet — Predictive Exception & Delay Alert System
Day 1: Foundation and environment
Day 4: Create workflow — routers + clean error handling wired in
Day 7: Authentication — auth router + protected-route error handling
Day 11: Reliability — structured logging, request-id correlation, and a
catch-all handler so an unexpected failure is always logged with full
detail server-side while the client only ever sees a safe, generic message.
Day 14: Deployment readiness — debug mode explicitly disabled, docs
exposure configurable, and /health actually checks the database rather
than just confirming the process is alive.
"""
import asyncio
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.config import DEBUG, DOCS_ENABLED, ENVIRONMENT
from app.database import engine
from app.exceptions import ConflictError, NotFoundError, UnauthorizedError
from app.logging_config import configure_logging, error_logger, request_logger
from app.routers import auth, drivers, vehicles, shipments

configure_logging()

TAGS_METADATA = [
    {"name": "auth", "description": "Registration, login, and the current authenticated user."},
    {"name": "shipments", "description": "The core resource. Owner-scoped: you only ever see your own shipments."},
    {"name": "drivers", "description": "Driver roster, owner-scoped."},
    {"name": "vehicles", "description": "Vehicle fleet, owner-scoped."},
    {"name": "system", "description": "Health check and service metadata — no auth required."},
]

app = FastAPI(
    title="PulseFleet",
    description=(
        "Predictive exception & delay alert system for fleet/shipment operations.\n\n"
        "Every resource below `/auth` is **owner-scoped**: create an account, log in, "
        "and everything you create is visible only to you. See `docs/API.md` in the "
        "repository for a full walkthrough (auth flow, error codes, and example "
        "requests), or use the **Authorize** button below to try authenticated "
        "requests directly from this page."
    ),
    version=__version__,
    openapi_tags=TAGS_METADATA,
    contact={"name": "PulseFleet", "url": "https://github.com/"},
    # debug is ALWAYS False (see app/config.py) — a debug FastAPI app
    # returns raw tracebacks to clients on unhandled errors, which is
    # exactly what Day 11's sanitized-500 design exists to prevent.
    debug=DEBUG,
    # Interactive docs can be switched off per-deployment via DOCS_ENABLED=false
    # without touching any route code.
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)

app.include_router(auth.router)
app.include_router(drivers.router)
app.include_router(vehicles.router)
app.include_router(shipments.router)


# ---------------------------------------------------------------------------
# Request-id + request logging middleware
# ---------------------------------------------------------------------------
# Every request gets a short correlation id, echoed back in the
# X-Request-ID response header and included in every log line and every
# error response body. When a person reports "I got an error", the
# request_id in what they see maps directly to one JSON log line
# server-side — no guessing which request out of thousands it was.

@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()

    response = await call_next(request)

    duration_ms = round((time.monotonic() - start) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    request_logger.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


# ---------------------------------------------------------------------------
# Exception handlers — every 4xx/5xx from the API conforms to ErrorResponse
# (detail, error_code, request_id) and never exposes stack traces, SQL, or
# internals. Expected 4xx outcomes (validation, not-found, conflict, auth)
# are NOT logged as errors — see logging_config.py's module docstring for
# why. Only genuinely unexpected exceptions hit error_logger.
# ---------------------------------------------------------------------------

@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"detail": exc.message, "error_code": exc.error_code, "request_id": _request_id(request)},
    )


@app.exception_handler(ConflictError)
async def conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": exc.message, "error_code": exc.error_code, "request_id": _request_id(request)},
    )


@app.exception_handler(UnauthorizedError)
async def unauthorized_handler(request: Request, exc: UnauthorizedError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": exc.message, "error_code": exc.error_code, "request_id": _request_id(request)},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Flatten Pydantic's default error list into one readable, actionable
    # message — which field, and why — instead of leaking raw internal
    # error objects to the client.
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body")
    message = f"{field}: {first['msg']}" if field else first["msg"]
    return JSONResponse(
        status_code=422,
        content={"detail": message, "error_code": "validation_error", "request_id": _request_id(request)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all for anything not already handled above — a bug, a DB
    connection drop, an unexpected third-party error, etc.

    The client NEVER sees the exception type, message, or traceback —
    only a generic message plus the request_id they can quote when
    reporting the issue. The full exception (type, message, traceback)
    is logged server-side via error_logger.exception, which is where an
    on-call engineer actually diagnoses it.
    """
    error_logger.exception(
        "unhandled exception",
        extra={
            "request_id": _request_id(request),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred. Please try again, and include the request_id if you contact support.",
            "error_code": "internal_error",
            "request_id": _request_id(request),
        },
    )


@app.get("/health", tags=["system"])
async def health_check() -> JSONResponse:
    """
    Liveness + readiness probe. Unlike a bare "the process is running"
    check, this actually verifies the database dependency is reachable —
    a process that's up but can't reach Postgres should NOT be reported
    healthy to a load balancer or orchestrator, since it can't serve
    real requests.

    Returns 200 with status "ok" when the database is reachable, or 503
    with status "degraded" and which dependency failed when it isn't —
    enough for an operator to act on, without leaking connection
    strings or raw driver exception text.
    """
    checks = {}
    healthy = True

    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except TimeoutError:
        checks["database"] = "timeout"
        healthy = False
    except Exception:
        checks["database"] = "unreachable"
        healthy = False

    body = {
        "status": "ok" if healthy else "degraded",
        "service": "PulseFleet",
        "version": __version__,
        "environment": ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@app.get("/", tags=["system"])
def root() -> dict:
    return {"message": "PulseFleet API is running. See /docs for API documentation."}

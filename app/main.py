"""
PulseFleet — Predictive Exception & Delay Alert System
Day 1: Foundation and environment
Day 4: Create workflow — routers + clean error handling wired in
"""
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import __version__
from app.exceptions import ConflictError, NotFoundError
from app.routers import drivers, vehicles, shipments

app = FastAPI(
    title="PulseFleet",
    description="Predictive exception & delay alert system for fleet/shipment operations.",
    version=__version__,
)

app.include_router(drivers.router)
app.include_router(vehicles.router)
app.include_router(shipments.router)


# ---------------------------------------------------------------------------
# Exception handlers — every 4xx/5xx from the API conforms to ErrorResponse
# (detail, error_code) and never exposes stack traces, SQL, or internals.
# ---------------------------------------------------------------------------

@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.message, "error_code": exc.error_code})


@app.exception_handler(ConflictError)
async def conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.message, "error_code": exc.error_code})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Flatten Pydantic's default error list into one readable message
    # instead of leaking raw internal error objects to the client.
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p != "body")
    message = f"{field}: {first['msg']}" if field else first["msg"]
    return JSONResponse(status_code=422, content={"detail": message, "error_code": "validation_error"})


@app.get("/health", tags=["system"])
def health_check() -> dict:
    """
    Basic liveness/readiness probe.
    Returns service status, current UTC time, and app version.
    """
    return {
        "status": "ok",
        "service": "PulseFleet",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/", tags=["system"])
def root() -> dict:
    return {"message": "PulseFleet API is running. See /docs for API documentation."}

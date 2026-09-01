"""
PulseFleet — Predictive Exception & Delay Alert System
Day 1: Foundation and environment
"""
from datetime import datetime, timezone

from fastapi import FastAPI

from app import __version__

app = FastAPI(
    title="PulseFleet",
    description="Predictive exception & delay alert system for fleet/shipment operations.",
    version=__version__,
)


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

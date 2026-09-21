"""
PulseFleet — Deployment readiness tests (Day 14)
"""
import pytest

from app.main import app

pytestmark = pytest.mark.asyncio


async def test_debug_mode_is_disabled():
    """FastAPI's debug flag must never be on — see app/config.py for why."""
    assert app.debug is False


async def test_health_check_reports_database_status(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert "environment" in body
    assert "version" in body


async def test_health_check_does_not_require_auth(client):
    """A load balancer/orchestrator hitting /health has no token — it must never 401."""
    resp = await client.get("/health")
    assert resp.status_code != 401

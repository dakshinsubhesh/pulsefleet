"""
PulseFleet — pytest fixtures for ownership tests (Day 8)

Runs the real ASGI app (app.main:app) against the live database configured
via DATABASE_URL — no mocking of the DB layer. Each test that needs a user
registers a fresh, uniquely-named one, so tests don't collide with each
other or with data left over from manual/earlier testing.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def unique_suffix() -> str:
    return uuid.uuid4().hex[:10]


def unique_username() -> str:
    return f"testuser_{unique_suffix()}"


async def register_and_login(client: AsyncClient) -> tuple[str, str]:
    """Registers a fresh user and returns (username, bearer_token)."""
    username = unique_username()
    password = "TestPassword123"

    resp = await client.post("/auth/register", json={"username": username, "password": password})
    assert resp.status_code == 201, resp.text

    resp = await client.post("/auth/login", data={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]

    return username, token


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

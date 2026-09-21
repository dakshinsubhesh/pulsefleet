"""
PulseFleet — pytest fixtures (Day 8 ownership tests; Day 12 isolated test DB)

CRITICAL ORDERING: DATABASE_URL is overridden to point at a dedicated
test database BEFORE `app.main` (and therefore `app.database`, which
builds the SQLAlchemy engine from DATABASE_URL at import time) is
imported anywhere. That's why the override happens at the very top of
this file, ahead of every other import. Every test file imports the app
via `from app.main import app` (directly or through this conftest), and
pytest always collects conftest.py first, so this is guaranteed to run
before the engine is constructed.

Test runs NEVER touch the development database (pulsefleet_db) — only
pulsefleet_test_db. This means:
- Tests can never corrupt or leave junk in data a developer is looking at.
- A developer's local dev-DB state can never make a test pass/fail by
  accident.
- CI can point TEST_DATABASE_URL at a throwaway database with no risk to
  anything real.
"""
import os

TEST_DATABASE_URL = (
    "postgresql+asyncpg://pulsefleet:PulseFleet2026@127.0.0.1:5432/pulsefleet_test_db"
)

# Plain assignment (not setdefault): this must win over whatever .env would
# otherwise supply. app.database calls load_dotenv() on import, and
# load_dotenv() does NOT override a variable already present in the
# environment — so setting it here first is what makes the override stick.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _migrate_test_database():
    """
    Runs `alembic upgrade head` against the test database once per test
    session, before any test executes. Reuses the exact same migration
    chain the real app deploys with (migrations/versions/) — the test
    database's schema is never hand-built or out of sync with what
    production would run.
    """
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to migrate test database at {TEST_DATABASE_URL}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    yield


@pytest_asyncio.fixture
async def client():
    # raise_app_exceptions=False matches real deployed behavior: Starlette's
    # ServerErrorMiddleware sends the handled error response AND then
    # re-raises the original exception (so a real ASGI server like uvicorn
    # can log it) — the default True would surface that re-raise to the
    # test as a Python exception instead of letting us assert on the
    # response the client actually received.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
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

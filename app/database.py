"""
PulseFleet — Async database session configuration
Day 3: Database setup
Day 11: Reliability — get_db now explicitly rolls back on any exception
before closing, so a failure partway through a request (a bug, an
unexpected DB error, anything not already caught by a route's own
try/except) can never leave a half-committed write behind.
"""
import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pulsefleet:changeme@127.0.0.1:5432/pulsefleet_db",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency — yields a request-scoped async session.

    Rolls back explicitly on any exception raised while the session is in
    use, THEN re-raises, so route handlers and exception handlers still
    see the original error — this only guarantees the session itself
    never lingers half-committed. A route that already does its own
    try/except/rollback around a specific operation (e.g. IntegrityError
    on a duplicate key) is unaffected: by the time an exception reaches
    here, that data is already safely rolled back or committed.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise

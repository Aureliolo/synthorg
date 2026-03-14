"""Fixtures for SQLite persistence unit tests."""

from typing import TYPE_CHECKING

import aiosqlite
import pytest

from synthorg.persistence.sqlite.migrations import run_migrations

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture
async def memory_db() -> AsyncGenerator[aiosqlite.Connection]:
    """Raw in-memory SQLite connection (no migrations)."""
    db = await aiosqlite.connect(":memory:")
    try:
        db.row_factory = aiosqlite.Row
        yield db
    finally:
        await db.close()


@pytest.fixture
async def migrated_db() -> AsyncGenerator[aiosqlite.Connection]:
    """In-memory SQLite connection with all schema migrations applied."""
    db = await aiosqlite.connect(":memory:")
    try:
        db.row_factory = aiosqlite.Row
        await run_migrations(db)
        yield db
    finally:
        await db.close()

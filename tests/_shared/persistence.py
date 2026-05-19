"""Test-only helpers for direct SQLite repository construction.

Production code never builds a private write context: every SQLite
repository receives its ``write_context`` from the backend so writes
serialize across the shared ``aiosqlite.Connection``. Tests that
exercise a single repository in isolation (no sibling repos on the
connection) use :func:`make_private_write_context` to satisfy the
required ``write_context`` constructor argument.

DO NOT import this module from application code. Each
``make_private_write_context()`` call returns its own isolated lock,
so two repositories that should share the backend write lock will
silently fail to serialize if both are constructed via this helper.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite  # lint-allow: persistence-boundary -- test-only SQLite helper

from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.sqlite.seen_claims_repo import SQLiteSeenClaimsRepository

_SEEN_CLAIMS_DDL: str = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "synthorg"
    / "persistence"
    / "sqlite"
    / "revisions"
    / "20260513000001_seen_claims.sql"
).read_text(encoding="utf-8")
"""Canonical ``seen_claims`` DDL, read once at import so the unit
double exercises the exact production schema (CHECK constraints +
index) rather than a hand-copied table that could silently drift.
Read at module load (sync) to keep the async helper free of blocking
filesystem I/O."""


def make_private_write_context() -> WriteContext:
    """Return a ``WriteContext`` backed by a fresh private ``asyncio.Lock``.

    Each call returns a new context-manager factory closed over its
    own lock; multiple calls produce independent serialization domains.
    Create one private write-context per shared connection per test:
    every repository attached to that connection MUST receive the same
    context so writes serialize across siblings, matching production
    where the backend hands out a single shared context. See the
    module-level warning above for why mixing contexts on one
    connection is unsafe.
    """
    lock = asyncio.Lock()

    @asynccontextmanager
    async def _cm() -> AsyncIterator[None]:
        async with lock:
            yield

    return _cm


@asynccontextmanager
async def make_sqlite_seen_claims() -> AsyncIterator[SQLiteSeenClaimsRepository]:
    """Yield a real ``SQLiteSeenClaimsRepository`` on an in-memory DB.

    Exercises the production SQL (``INSERT ... ON CONFLICT DO NOTHING``,
    the CHECK constraints, the expiry index) without a container, so
    unit tests assert real dedup behaviour rather than a stub's. One
    shared connection + one shared write context mirrors how the
    backend hands repositories their serialisation domain.
    """
    db = await aiosqlite.connect(":memory:")
    try:
        await db.executescript(_SEEN_CLAIMS_DDL)
        await db.commit()
        repo = SQLiteSeenClaimsRepository(
            db,
            write_context=make_private_write_context(),
        )
        yield repo
    finally:
        await db.close()

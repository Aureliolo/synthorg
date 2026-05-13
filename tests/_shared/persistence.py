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

from synthorg.persistence.sqlite._shared import WriteContext


def make_private_write_context() -> WriteContext:
    """Return a ``WriteContext`` backed by a fresh private ``asyncio.Lock``.

    Each call returns a new context-manager factory closed over its
    own lock; multiple calls produce independent serialization
    domains. Use one per repository per test so concurrent operations
    on the same repo still serialize correctly.
    """
    lock = asyncio.Lock()

    @asynccontextmanager
    async def _cm() -> AsyncIterator[None]:
        async with lock:
            yield

    return _cm

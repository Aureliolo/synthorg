"""Conformance tests for ``PersistenceBackend.write_context``.

Both backends must expose ``write_context`` as a one-shot async
context manager that enters, yields ``None``, and exits cleanly.
Each call returns a fresh context manager (re-entry across separate
calls is supported).

This file covers the cross-backend contract. SQLite's stricter
mutual-exclusion guarantee is exercised in
``test_sqlite_write_context_serializes.py`` (single-arm by design;
Postgres ``write_context`` is a no-op).
"""

import pytest

from synthorg.persistence.protocol import PersistenceBackend


@pytest.mark.integration
async def test_write_context_enters_and_exits(
    backend: PersistenceBackend,
) -> None:
    async with backend.write_context() as first:
        assert first is None
    async with backend.write_context() as second:
        assert second is None

"""Typed accessors for the backend-specific raw database handle.

``PersistenceBackend.get_db`` returns ``object`` because the handle is
backend-specific (an ``aiosqlite.Connection`` for SQLite, an
``AsyncConnectionPool`` for Postgres). These helpers let wiring code that
already knows the active backend (via ``backend_name``) recover the
concrete handle type WITHOUT naming ``aiosqlite`` / ``psycopg`` itself,
so ``api`` / ``meta`` wiring stays inside the persistence boundary while
still passing a correctly-typed handle to backend-specific repositories.
"""

from collections.abc import Callable
from typing import cast

import aiosqlite
from psycopg_pool import AsyncConnectionPool

from synthorg.persistence.protocol import PersistenceBackend


def sqlite_connection(backend: PersistenceBackend) -> aiosqlite.Connection:
    """Recover the SQLite backend's raw ``aiosqlite.Connection``.

    The caller asserts (via ``backend_name == "sqlite"``) that *backend*
    is the SQLite variant; the opaque ``get_db`` handle is narrowed to
    the concrete type so backend-specific repositories type-check.

    Returns:
        The raw ``aiosqlite.Connection``.
    """
    return cast("aiosqlite.Connection", backend.get_db())


def postgres_pool(backend: PersistenceBackend) -> AsyncConnectionPool:
    """Recover the Postgres backend's raw ``AsyncConnectionPool``.

    Returns:
        The raw psycopg ``AsyncConnectionPool``.
    """
    return cast("AsyncConnectionPool", backend.get_db())


def postgres_pool_getter(
    backend: PersistenceBackend,
) -> Callable[[], AsyncConnectionPool]:
    """Return a zero-argument getter for the backend's psycopg pool.

    Used where a consumer defers pool acquisition (the secret-backend
    factory resolves the pool lazily at first use rather than at wiring
    time).

    Returns:
        A callable yielding the raw ``AsyncConnectionPool``.
    """
    return lambda: postgres_pool(backend)

"""Fixtures for parametrized persistence conformance tests.

Exposes a single ``backend`` fixture parametrized over
``["sqlite", "postgres"]``.  Each test that consumes it runs once
against SQLite and once against Postgres, both freshly connected and
migrated.

SQLite arm:
    Uses ``migrations.copy_revisions`` to seed a per-test SQLite file
    on-disk tempfile database, so concurrent xdist workers do not
    contend on the shared revisions directory lock.

Postgres arm:
    Uses a session-scoped ``testcontainers.postgres.PostgresContainer``
    running ``postgres:18-alpine`` as the shared server, then creates
    a unique per-test database on top.  The container starts once per
    xdist worker and is reused across all tests that need Postgres so
    container startup (~5s) amortizes across the suite.  Tests are
    automatically skipped when Docker is unavailable on the host.
"""

import asyncio
import contextlib
import shutil
import sys
import uuid
import warnings
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr

from synthorg.persistence import migrations
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Use SelectorEventLoop on Windows so psycopg async mode works.

    psycopg 3 refuses to run under ``ProactorEventLoop`` (the default
    Windows asyncio loop since 3.8).  This fixture overrides the
    pytest-asyncio default policy for tests in this directory only,
    leaving other test suites on the default policy.

    The stdlib policy API is deprecated in Python 3.14 (scheduled for
    removal in 3.16) but pytest-asyncio 1.3 still consumes it; we
    silence the DeprecationWarning locally until pytest-asyncio
    exposes a ``loop_factory`` hook.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sys.platform == "win32":
            return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore,unreachable]


def _docker_available() -> bool:
    """Return ``True`` if the Docker CLI is reachable.

    testcontainers talks to the Docker daemon via the socket; the CLI
    check is a cheap proxy and avoids importing docker-py up front.
    """
    return shutil.which("docker") is not None


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start one shared Postgres 18 container per pytest session.

    Skips tests that depend on it when Docker is unavailable OR when
    container startup fails for any reason (daemon not running,
    permission denied, image pull failure, port collision, etc.).
    A bare ``container.start()`` would otherwise raise an error that
    pytest reports as a test failure rather than a skip, which is
    confusing when the real problem is a missing dev dependency.
    """
    if not _docker_available():
        pytest.skip("Docker is required for the postgres conformance arm")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:18-alpine")
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"Could not start Postgres test container: {exc}")
    try:
        yield container
    finally:
        container.stop()


def _container_host_ipv4(container: PostgresContainer) -> str:
    """Return the container's host as an IPv4 literal.

    ``PostgresContainer.get_container_host_ip()`` returns ``"localhost"``
    on Docker Desktop (Windows / macOS).  Go's default resolver then
    prefers IPv6 ``::1``, and Docker Desktop's vpnkit/gvisor port
    proxy has flaky IPv6 handling right after container start, causing
    intermittent i/o timeouts on the first ``migrations.migrate_apply``.
    Forcing IPv4 sidesteps the race entirely.
    """
    host: str = container.get_container_host_ip()
    if host in {"localhost", "::1", ""}:
        return "127.0.0.1"
    return host


async def _create_postgres_backend(
    container: PostgresContainer,
    db_name: str,
) -> PostgresPersistenceBackend:
    """Create a test database on *container* and return a migrated backend.

    On any failure after ``CREATE DATABASE`` (backend construct,
    ``connect()``, ``migrate()``) the partially-created database is
    dropped and the backend is disconnected so the session does not
    accumulate orphaned databases and dangling pools.  The
    ``finally``/cleanup in the outer ``backend`` fixture only runs
    once this helper has returned a successfully-created backend.
    """
    host = _container_host_ipv4(container)
    port = int(container.get_exposed_port(5432))
    admin_conninfo = psycopg.conninfo.make_conninfo(
        host=host,
        port=port,
        user=container.username,
        password=container.password,
        dbname=container.dbname,
    )
    async with await psycopg.AsyncConnection.connect(
        admin_conninfo, autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
        )

    config = PostgresConfig(
        host=host,
        port=port,
        database=db_name,
        username=container.username,
        password=SecretStr(container.password),
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=10.0,
        connect_timeout_seconds=5.0,
    )
    backend = PostgresPersistenceBackend(config)
    try:
        await backend.connect()
        await backend.migrate()
    except BaseException:
        with contextlib.suppress(Exception):
            await backend.disconnect()
        with contextlib.suppress(Exception):
            await _drop_postgres_database(container, db_name)
        raise
    return backend


async def _drop_postgres_database(
    container: PostgresContainer,
    db_name: str,
) -> None:
    """Terminate remaining sessions on *db_name* and drop it."""
    admin_conninfo = psycopg.conninfo.make_conninfo(
        host=_container_host_ipv4(container),
        port=int(container.get_exposed_port(5432)),
        user=container.username,
        password=container.password,
        dbname=container.dbname,
    )
    async with await psycopg.AsyncConnection.connect(
        admin_conninfo, autocommit=True
    ) as admin:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid != pg_backend_pid()",
            (db_name,),
        )
        await admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name))
        )


@pytest.fixture(params=["sqlite", "postgres"], ids=["sqlite", "postgres"])
async def backend(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[PersistenceBackend]:
    """Yield a connected, migrated backend parametrized over impls.

    The fixture resolves sub-dependencies inline (no
    ``getfixturevalue`` across async boundaries) so pytest-asyncio can
    drive both setup and teardown as a single async generator.
    """
    backend_name = request.param
    if backend_name == "sqlite":
        db_path = tmp_path / "conformance.db"
        rev_path = migrations.copy_revisions(tmp_path / "revisions", backend="sqlite")
        await migrations.migrate_apply(
            migrations.to_sqlite_url(str(db_path)),
            revisions_path=rev_path,
        )
        sqlite_backend = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
        await sqlite_backend.connect()
        try:
            yield sqlite_backend
        finally:
            await sqlite_backend.disconnect()
    elif backend_name == "postgres":
        container = request.getfixturevalue("postgres_container")
        db_name = f"test_{uuid.uuid4().hex}"
        pg_backend = await _create_postgres_backend(container, db_name)
        try:
            yield pg_backend
        finally:
            try:
                await pg_backend.disconnect()
            finally:
                # Always drop the per-test database even if disconnect
                # fails, otherwise the shared container accumulates
                # orphaned databases over the session.
                await _drop_postgres_database(container, db_name)
    else:  # pragma: no cover - defensive
        msg = f"Unknown conformance backend: {backend_name}"
        raise ValueError(msg)

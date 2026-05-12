"""Fixtures for persistence integration tests."""

import asyncio
import shutil
import sys
import uuid
import warnings
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg
import pytest
from psycopg import sql
from pydantic import SecretStr

from synthorg.persistence import migrations
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """Use SelectorEventLoop on Windows so psycopg async mode works.

    Scoped to the integration directory so other test suites keep
    their default ProactorEventLoop.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sys.platform == "win32":
            return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore]
        return asyncio.DefaultEventLoopPolicy()  # type: ignore[attr-defined,unused-ignore,unreachable]


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Return a temporary on-disk database path."""
    return str(tmp_path / "test.db")


async def _isolated_sqlite_migrate(db_path: str, tmp_path: Path) -> None:
    """Apply SQLite migrations against a per-test isolated revisions copy.

    Each xdist worker already has its own SQLite file (per ``tmp_path``),
    so yoyo's DB-level lock cannot contend across workers; the per-test
    revisions copy is kept so the on-disk layout matches the
    production install.  Callers are responsible for opening the
    backend connection themselves.
    """
    revisions_path = migrations.copy_revisions(
        tmp_path / f"sqlite_revisions_{uuid.uuid4().hex}",
        backend="sqlite",
    )
    await migrations.migrate_apply(
        migrations.to_sqlite_url(db_path),
        revisions_path=revisions_path,
        backend="sqlite",
    )


@pytest.fixture
def sqlite_migrate(
    tmp_path: Path,
) -> Callable[[str], Awaitable[None]]:
    """Return an async helper that migrates a SQLite DB in isolation.

    Used by tests that manage their own ``SQLitePersistenceBackend``
    instance rather than going through ``on_disk_backend``.  Each
    call to the returned helper operates on a fresh revisions copy
    so multiple reconnects within a single test stay isolated.
    """

    async def _migrate(db_path: str) -> None:
        await _isolated_sqlite_migrate(db_path, tmp_path)

    return _migrate


@pytest.fixture
async def on_disk_backend(
    db_path: str,
    tmp_path: Path,
) -> AsyncGenerator[SQLitePersistenceBackend]:
    """Connected + migrated on-disk SQLite backend.

    Each test gets an isolated copy of the sqlite revisions
    directory via ``migrations.copy_revisions``.  Yoyo's lock is
    DB-level, not filesystem, so per-worker SQLite files never
    contend; the copy keeps the on-disk layout symmetric with
    production.
    """
    backend = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await backend.connect()
    try:
        await _isolated_sqlite_migrate(db_path, tmp_path)
        yield backend
    finally:
        await backend.disconnect()


def _docker_available() -> bool:
    """Return ``True`` if the Docker CLI is reachable."""
    return shutil.which("docker") is not None


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Start one shared Postgres 18 container per pytest session."""
    if not _docker_available():
        pytest.skip("Docker is required for postgres integration tests")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:18-alpine")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def postgres_backend(
    postgres_container: PostgresContainer,
) -> AsyncIterator[PostgresPersistenceBackend]:
    """Yield a connected, migrated PostgresPersistenceBackend.

    Creates a unique database on the shared container so tests stay
    isolated, migrates it via Atlas, hands the backend to the test,
    then drops the database on teardown.
    """
    db_name = f"test_{uuid.uuid4().hex}"
    admin_conninfo = psycopg.conninfo.make_conninfo(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        dbname=postgres_container.dbname,
    )
    async with await psycopg.AsyncConnection.connect(
        admin_conninfo, autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
        )

    config = PostgresConfig(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        database=db_name,
        username=postgres_container.username,
        password=SecretStr(postgres_container.password),
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=10.0,
        connect_timeout_seconds=5.0,
    )
    backend = PostgresPersistenceBackend(config)
    await backend.connect()
    try:
        await backend.migrate()
        yield backend
    finally:
        await backend.disconnect()
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


_TIMESCALEDB_IMAGE = "timescale/timescaledb:2.26.2-pg18-oss"


@pytest.fixture(scope="session")
def timescaledb_container() -> Iterator[PostgresContainer]:
    """Start one shared TimescaleDB (Postgres 18 OSS) container per session.

    Pins the OSS image (Apache 2.0 only, no Timescale License
    features) so tests never exercise licensed functionality.  Tests
    that depend on the TimescaleDB extension use this fixture
    instead of ``postgres_container``; the image includes both
    vanilla Postgres and the ``timescaledb`` extension binary so the
    base schema still migrates cleanly via Atlas.
    """
    if not _docker_available():
        pytest.skip("Docker is required for TimescaleDB integration tests")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(_TIMESCALEDB_IMAGE)
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
async def timescaledb_backend(
    timescaledb_container: PostgresContainer,
) -> AsyncIterator[PostgresPersistenceBackend]:
    """Yield a connected, migrated PostgresPersistenceBackend with TimescaleDB on.

    Sets ``enable_timescaledb=True`` with 1-day chunk intervals so
    hypertables are created and observable in tests.  Each test gets
    a fresh database on the shared container to stay isolated.
    """
    db_name = f"ts_{uuid.uuid4().hex}"
    admin_conninfo = psycopg.conninfo.make_conninfo(
        host=timescaledb_container.get_container_host_ip(),
        port=int(timescaledb_container.get_exposed_port(5432)),
        user=timescaledb_container.username,
        password=timescaledb_container.password,
        dbname=timescaledb_container.dbname,
    )
    async with await psycopg.AsyncConnection.connect(
        admin_conninfo, autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
        )

    config = PostgresConfig(
        host=timescaledb_container.get_container_host_ip(),
        port=int(timescaledb_container.get_exposed_port(5432)),
        database=db_name,
        username=timescaledb_container.username,
        password=SecretStr(timescaledb_container.password),
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=10.0,
        connect_timeout_seconds=5.0,
        enable_timescaledb=True,
        cost_records_chunk_interval="1 day",
        audit_entries_chunk_interval="1 day",
    )
    backend = PostgresPersistenceBackend(config)
    await backend.connect()
    try:
        await backend.migrate()
        yield backend
    finally:
        await backend.disconnect()
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


@pytest.fixture
async def postgres_backend_factory(
    postgres_backend: PostgresPersistenceBackend,
) -> AsyncIterator[Callable[[], Awaitable[PostgresPersistenceBackend]]]:
    """Yield a factory that returns extra PostgresPersistenceBackend instances.

    Each call builds a brand-new backend bound to the same DSN as
    ``postgres_backend`` but with its own connection pool. Tests use
    this to surface pool-local races that a single-instance test
    cannot expose: prepared-statement cache, channel pinning, and
    any other shared state inside one pool coincidentally serialises
    concurrent writers that hit the same pool.

    Created backends are disconnected in fixture teardown. The
    factory does NOT re-run migrations -- the first ``postgres_backend``
    fixture already migrated the shared database.
    """
    created: list[PostgresPersistenceBackend] = []
    base_config = postgres_backend._config

    async def _make() -> PostgresPersistenceBackend:
        backend = PostgresPersistenceBackend(base_config)
        await backend.connect()
        created.append(backend)
        return backend

    try:
        yield _make
    finally:
        for b in created:
            await b.disconnect()

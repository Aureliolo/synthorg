"""Fixtures for persistence integration tests."""

import asyncio
import shutil
import sys
import uuid
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
)
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import psycopg.conninfo
import pytest
from psycopg import sql
from pydantic import SecretStr

from synthorg.persistence import migrations
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared.postgres_proxy import PostgresContainerProxy
from tests._shared.postgres_proxy import from_env as _proxy_from_env
from tests._shared.postgres_proxy import from_testcontainer as _proxy_from_testcontainer
from tests._shared.postgres_template import (
    clone_from_template,
    drop_test_database,
    ensure_pg_template,
    xdist_shared_dir,
)

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer


if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``SelectorEventLoop`` on Windows so psycopg async mode works.

        psycopg 3's async path requires a select-style loop on Windows
        (it does not integrate with ``ProactorEventLoop``'s IOCP).
        Scoped to the integration directory so other test suites keep
        their default ``ProactorEventLoop``.
        """
        return {"selector": asyncio.SelectorEventLoop}


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
def postgres_container(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[PostgresContainerProxy]:
    """Start one shared Postgres 18 container per pytest session.

    In CI ``services: postgres`` exposes a server-managed instance via
    ``SYNTHORG_TEST_POSTGRES_HOST`` / ``PORT`` / ``USER`` / ``PASSWORD``
    / ``DB``; when those env vars are set the testcontainers start-up
    is skipped entirely and a proxy built directly from env is yielded.
    Per-test database isolation still works because ``postgres_backend``
    clones a unique ``test_<uuid>`` DB on the shared server.

    The migrated template DB is ensured here (idempotent, cross-worker
    coordinated) so every consumer -- ``postgres_backend`` and the
    ``test_wp1_restart_safety`` factory -- clones it instead of
    replaying the migration chain. In CI the conformance suite's
    ``pytest_sessionstart`` has usually already built it on the same
    shared server, so this call hits the sentinel and returns instantly.
    """
    env_proxy = _proxy_from_env()
    if env_proxy is not None:
        asyncio.run(ensure_pg_template(env_proxy, xdist_shared_dir(tmp_path_factory)))
        yield env_proxy
        return

    if not _docker_available():
        pytest.skip("Docker is required for postgres integration tests")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:18-alpine")
    container.start()
    try:
        proxy = _proxy_from_testcontainer(container)
        asyncio.run(ensure_pg_template(proxy, xdist_shared_dir(tmp_path_factory)))
        yield proxy
    finally:
        container.stop()


@pytest.fixture
async def postgres_backend(
    postgres_container: PostgresContainerProxy,
) -> AsyncIterator[PostgresPersistenceBackend]:
    """Yield a connected PostgresPersistenceBackend on a fresh cloned DB.

    Clones the migrated template (ensured by the ``postgres_container``
    fixture) into a unique ``test_<uuid>`` database -- a near-instant
    file copy instead of a per-test ``migrate()`` -- then drops it on
    teardown. Per-test isolation is unchanged: each test still gets its
    own database.
    """
    db_name = f"test_{uuid.uuid4().hex}"
    backend = await clone_from_template(postgres_container, db_name)
    try:
        yield backend
    finally:
        # Drop even if disconnect raises, else a failed disconnect leaks
        # the per-test database onto the shared server for the session.
        try:
            await backend.disconnect()
        finally:
            await drop_test_database(postgres_container, db_name)


_TIMESCALEDB_IMAGE = "timescale/timescaledb:2.26.2-pg18-oss"


@pytest.fixture(scope="session")
def timescaledb_container() -> Iterator[PostgresContainer]:
    """Start one shared TimescaleDB (Postgres 18 OSS) container per session.

    Pins the OSS image (Apache 2.0 only, no Timescale License
    features) so tests never exercise licensed functionality.  Tests
    that depend on the TimescaleDB extension use this fixture
    instead of ``postgres_container``; the image includes both
    vanilla Postgres and the ``timescaledb`` extension binary so the
    base schema still migrates cleanly via yoyo.
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

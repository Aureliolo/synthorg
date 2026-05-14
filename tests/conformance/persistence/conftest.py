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
    Uses ONE ``testcontainers.postgres.PostgresContainer`` running
    ``postgres:18-alpine`` shared across every xdist worker via a
    ``filelock``-coordinated state file in
    ``tmp_path_factory.getbasetemp().parent`` (the directory pytest-xdist
    treats as common ground for all workers in a single invocation).
    The first worker to acquire the lock starts the container and
    records its connection info; later workers read the same record.
    Workers reference-count their use; the worker that drops the count
    to zero stops + removes the container by id via ``docker-py`` so
    the cleanup runs exactly once regardless of which worker exits
    last. Tests are automatically skipped when Docker is unavailable.
"""

import asyncio
import contextlib
import json
import shutil
import sys
import uuid
import warnings
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Final

import psycopg
import pytest
from filelock import FileLock
from psycopg import sql
from pydantic import SecretStr

from synthorg.observability import get_logger, safe_error_description
from synthorg.persistence import migrations
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

logger = get_logger(__name__)


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


class _PostgresContainerProxy:
    """Connection-info handle for a Postgres container shared across xdist workers.

    Exposes the subset of the ``testcontainers.postgres.PostgresContainer``
    surface this conftest actually consumes (``get_container_host_ip``,
    ``get_exposed_port``, ``username``, ``password``, ``dbname``). Holding
    a proxy rather than the real ``PostgresContainer`` object lets every
    xdist worker treat the container as an opaque dependency without
    each worker carrying its own per-process Docker SDK handle.
    """

    __slots__ = ("_host", "_port", "dbname", "password", "username")

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        dbname: str,
    ) -> None:
        self._host = host
        self._port = port
        self.username = username
        self.password = password
        self.dbname = dbname

    def get_container_host_ip(self) -> str:
        return self._host

    def get_exposed_port(self, _internal_port: int) -> str:
        return str(self._port)


def _stop_container_by_id(container_id: str) -> None:
    """Stop and remove a Docker container by id using docker-py.

    The "last worker out" decrements the shared refcount in the state
    file and calls this helper. The starter worker no longer owns the
    teardown directly because xdist may schedule it to finish before
    other workers; routing cleanup through the Docker SDK lets any
    worker perform it given just the container id.

    Swallows Docker SDK errors so a missing image or already-removed
    container does not break a test session that otherwise passed --
    the worst case is a stray container that ``testcontainers``' Ryuk
    reaper cleans up once the parent pytest process exits.
    """
    try:
        import docker
    except ImportError:  # pragma: no cover - docker-py ships with testcontainers
        return
    docker_any: Any = docker
    try:
        client = docker_any.from_env()
        container = client.containers.get(container_id)
        container.stop(timeout=5)
        container.remove(force=True, v=True)
    except docker_any.errors.NotFound:  # pragma: no cover - already cleaned up
        return
    except Exception as exc:
        logger.warning(
            "postgres_container.teardown_failed",
            container_id=container_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _acquire_shared_postgres(state_file: Path) -> dict[str, Any]:
    """Read the shared state file or start the container as the first worker.

    Caller holds the ``FileLock`` so this body is serialised across
    workers. The first worker into the lock starts the container and
    writes the connection info; later workers bump ``refcount`` on the
    existing record. If a previous worker already recorded a
    ``skip_reason``, peers reraise it via ``pytest.skip`` so every
    worker surfaces the same cause.
    """
    from testcontainers.postgres import PostgresContainer

    if state_file.exists():
        try:
            data: dict[str, Any] = json.loads(state_file.read_text())
        except json.JSONDecodeError:
            state_file.unlink(missing_ok=True)
        else:
            if data.get("skip_reason"):
                pytest.skip(data["skip_reason"])
            data["refcount"] = int(data.get("refcount", 0)) + 1
            state_file.write_text(json.dumps(data))
            return data
    try:
        container = PostgresContainer("postgres:18-alpine")
        container.start()
    except Exception as exc:
        reason = f"Could not start Postgres test container: {type(exc).__name__}: {exc}"
        state_file.write_text(json.dumps({"skip_reason": reason}))
        pytest.skip(reason)
    data = {
        "container_id": container.get_wrapped_container().id,
        "host": container.get_container_host_ip(),
        "port": int(container.get_exposed_port(5432)),
        "username": container.username,
        "password": container.password,
        "dbname": container.dbname,
        "refcount": 1,
    }
    state_file.write_text(json.dumps(data))
    return data


def _release_shared_postgres(state_file: Path) -> None:
    """Decrement the refcount and tear down when this worker is the last.

    Caller holds the ``FileLock``. Reads the latest state, decrements
    ``refcount``, and if the count hits zero stops + removes the
    container by id and deletes the state file. Missing files and
    skip-reason placeholders short-circuit so the cleanup never raises.
    """
    try:
        current = json.loads(state_file.read_text())
    except FileNotFoundError, json.JSONDecodeError:
        # Missing file is the normal "another worker already tore down"
        # case. A partial write that left invalid JSON falls into the
        # same bucket: drop the stale file rather than cascade the
        # decode error across every other worker still holding the
        # FileLock. The container will be cleaned up by testcontainers'
        # Ryuk reaper once the parent pytest process exits.
        state_file.unlink(missing_ok=True)
        return
    if current.get("skip_reason"):
        return
    current["refcount"] = max(0, int(current.get("refcount", 1)) - 1)
    if current["refcount"] == 0:
        container_id = current.get("container_id")
        if container_id is not None:
            _stop_container_by_id(container_id)
        state_file.unlink(missing_ok=True)
    else:
        state_file.write_text(json.dumps(current))


@pytest.fixture(scope="session")
def postgres_container(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Iterator[_PostgresContainerProxy]:
    """Yield ONE Postgres 18 container shared across every xdist worker.

    The first worker to acquire the inter-process ``FileLock`` starts
    the container (~5s, image pulls excluded), records its connection
    info under ``tmp_path_factory.getbasetemp().parent``, and bumps a
    ``refcount`` field. Later workers acquire the same lock, see the
    state file, and just increment ``refcount``. On teardown each
    worker decrements; the worker that drops the count to zero stops
    and removes the container by id. The starter's local container
    handle is intentionally not the cleanup hook because xdist can
    schedule the starter to exit first.

    Skips when Docker is unavailable or when container startup fails
    for any reason. When the first worker into the lock can't start
    the container, it records the failure in the state file so peers
    skip cleanly too (rather than each peer trying the same start in
    sequence and emitting 8 different skip reasons).
    """
    if not _docker_available():
        pytest.skip("Docker is required for the postgres conformance arm")

    if worker_id == "master":
        shared_dir = tmp_path_factory.getbasetemp()
    else:
        shared_dir = tmp_path_factory.getbasetemp().parent
    state_file = shared_dir / "postgres_container_state.json"
    lock_path = str(shared_dir / "postgres_container.lock")
    # 60s lock timeout guards against the pathological case where a
    # worker dies holding the lock; peers fall through to a fresh
    # acquire instead of wedging the whole suite.
    lock_timeout: Final[int] = 60

    with FileLock(lock_path, timeout=lock_timeout):
        data = _acquire_shared_postgres(state_file)

    proxy = _PostgresContainerProxy(
        host=data["host"],
        port=data["port"],
        username=data["username"],
        password=data["password"],
        dbname=data["dbname"],
    )
    try:
        yield proxy
    finally:
        with FileLock(lock_path, timeout=lock_timeout):
            _release_shared_postgres(state_file)


def _container_host_ipv4(container: _PostgresContainerProxy) -> str:
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
    container: _PostgresContainerProxy,
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
    container: _PostgresContainerProxy,
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

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
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Final

import pytest
from filelock import FileLock

from synthorg.observability import get_logger, safe_error_description
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared import JsonDict
from tests._shared.postgres_proxy import PostgresContainerProxy
from tests._shared.postgres_proxy import from_env as _proxy_from_env
from tests._shared.postgres_template import (
    clone_from_template,
    drop_test_database,
    run_pg_template_build,
    xdist_shared_dir,
)

logger = get_logger(__name__)


if sys.platform == "win32":  # pragma: no cover -- Windows-only branch

    def pytest_asyncio_loop_factories(
        config: pytest.Config,
        item: pytest.Item,
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        """Use ``SelectorEventLoop`` on Windows so psycopg async mode works.

        psycopg 3's async path does not support ``ProactorEventLoop``
        (the default Windows asyncio loop since 3.8); it requires a
        select-style loop. Scoped to this directory so other test
        suites keep their default factory.
        """
        return {"selector": asyncio.SelectorEventLoop}


def _docker_available() -> bool:
    """Return ``True`` if the Docker daemon is reachable.

    testcontainers-python talks to the daemon directly via docker-py
    (no CLI binary required), so probe the daemon socket / DOCKER_HOST
    rather than ``shutil.which("docker")`` -- a CLI check would skip
    daemon-only environments (containerised CI, Docker-in-Docker,
    socket-mounted runners) where the conformance arm should still run.
    """
    try:
        import docker
    except ImportError:
        return False
    docker_any: Any = docker  # type: ignore[explicit-any]  # docker-py ships no type stubs
    try:
        client = docker_any.from_env()
        client.ping()
    except Exception:
        return False
    return True


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
    docker_any: Any = docker  # type: ignore[explicit-any]  # docker-py ships no type stubs
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


def _acquire_shared_postgres(state_file: Path) -> JsonDict:
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
            data: JsonDict = json.loads(state_file.read_text())
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
    # ``container.start()`` blocks on testcontainers' internal readiness
    # probe (``pg_isready`` via the wait strategy), but on Docker Desktop
    # the vpnkit / gvisor port-proxy occasionally takes another ~1-2s
    # before the published port routes cleanly. Probe once from the host
    # side so the first peer worker that arrives sees an accepting
    # connection rather than racing the proxy.
    _wait_for_postgres_accept(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        timeout_seconds=15.0,
    )
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


def _wait_for_postgres_accept(
    *,
    host: str,
    port: int,
    timeout_seconds: float,
) -> None:
    """Poll a TCP connect against ``host:port`` until it accepts.

    Bounds total wait by ``timeout_seconds``; on expiry returns
    without raising so the existing testcontainers wait strategy
    handles the error path (``container.start()`` would already have
    raised if postgres itself never came up). The poll is a thin
    belt-and-braces guard against the Docker Desktop port-proxy
    accept gap; production deployments never hit it.
    """
    import socket
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)


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


# Module-level cache for the postgres container handle, populated by
# ``pytest_sessionstart`` BEFORE any per-test timer starts. ``None``
# means "not yet resolved" (sessionstart hook didn't run -- should
# not happen) or "env-var bypass active" depending on which key is
# set. See the ``postgres_container`` fixture and the
# ``pytest_sessionstart`` hook below for the contract.
_POSTGRES_CONTAINER_STATE: JsonDict = {}


def _xdist_shared_dir(session: pytest.Session) -> Path:
    """Run-wide xdist temp dir; delegates to the shared helper."""
    return xdist_shared_dir(session.config._tmp_path_factory)  # type: ignore[attr-defined]


def _pre_acquire_postgres_container_state(session: pytest.Session) -> None:
    """Pre-acquire the shared Postgres container BEFORE any per-test timer.

    The cross-worker ``FileLock`` in :func:`_acquire_shared_postgres` is
    session-level coordination, not per-test work. If we leave it in the
    fixture's setup phase (even as ``scope="session"``, even with
    ``autouse=True``), pytest will run the setup during the FIRST
    referencing test's ``pytest_runtest_setup`` -- which IS covered by
    ``pytest-timeout``. Workers queued behind the container starter
    spend their wait in the FileLock poll loop; if total wait exceeds
    the per-test 30s budget the worker dies with no useful diagnostic
    (observed previously on the sibling ``migrated_db`` template build,
    where moving the coordination into an autouse session fixture
    killed every test on a queued worker at exactly t+30s).

    ``pytest_sessionstart`` runs before any test, is NOT covered by
    ``pytest-timeout``, and runs once per xdist worker subprocess.
    Resolving the lock + container start here is the correct shape;
    the ``postgres_container`` fixture below just reads the cached
    state and yields a proxy.

    Layered short-circuits (preserved from the previous fixture):

    1. CI sets ``SYNTHORG_TEST_POSTGRES_*`` env vars to point at the
       ``services: postgres`` instance -- no FileLock, no Docker.
    2. Local dev without Docker -- record a ``skip_reason`` so the
       fixture's pytest.skip surfaces a clean cause.
    3. Local dev with Docker -- acquire the FileLock, run
       ``_acquire_shared_postgres``, record the resulting data.

    The fixture-level teardown (refcount decrement + container stop on
    the last worker) stays inside the fixture's ``finally`` clause;
    the FileLock wait during teardown is on a non-critical path
    (workers are exiting anyway) so it doesn't need to move to
    ``pytest_sessionfinish``.

    The forensic ``sys.stderr.write`` at the top lets us confirm in CI
    logs whether this hook actually fires on each xdist worker. The
    fixture below has a matching write; a missing pair tells us
    conftest discovery is broken on the worker, a present hook write +
    missing fixture-time state tells us the module instance is split
    across importers. Stays in production: pytest_sessionstart runs
    once per session so the cost is one line of stderr per worker.
    """
    _worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    sys.stderr.write(
        f"[conftest:conformance/persistence] pytest_sessionstart fired "
        f"(worker={_worker}, state_id={id(_POSTGRES_CONTAINER_STATE)})\n"
    )
    sys.stderr.flush()
    env_proxy = _proxy_from_env()
    if env_proxy is not None:
        # Build the migrated template once on the shared CI server here
        # (sessionstart, NOT covered by pytest-timeout) so per-test
        # setup clones it instead of replaying the migration chain.
        run_pg_template_build(env_proxy, _xdist_shared_dir(session))
        _POSTGRES_CONTAINER_STATE["mode"] = "env"
        return

    if not _docker_available():
        _POSTGRES_CONTAINER_STATE["mode"] = "skip"
        _POSTGRES_CONTAINER_STATE["skip_reason"] = (
            "Docker is required for the postgres conformance arm"
        )
        return

    shared_dir = _xdist_shared_dir(session)
    state_file = shared_dir / "postgres_container_state.json"
    lock_path = str(shared_dir / "postgres_container.lock")
    # Catastrophe ceiling, aligned with ``tests/conftest.py``'s
    # ``_FILE_LOCK_TIMEOUT_SECONDS``. Refcount semantics here require
    # every worker to acquire the lock to bump the refcount, so we
    # cannot poll-and-skip the way ``_get_template_db`` does. Followers
    # genuinely block for the leader's container start + readiness
    # poll; cold-cache image pulls on slow CI runners can take several
    # minutes, so the previous 180s was tight enough to trip when the
    # leader hit the long tail.
    lock_timeout: Final[int] = 600
    with FileLock(lock_path, timeout=lock_timeout):
        try:
            data = _acquire_shared_postgres(state_file)
        except pytest.skip.Exception as exc:  # pragma: no cover -- skip path
            _POSTGRES_CONTAINER_STATE["mode"] = "skip"
            _POSTGRES_CONTAINER_STATE["skip_reason"] = str(exc)
            return
    _POSTGRES_CONTAINER_STATE["mode"] = "container"
    _POSTGRES_CONTAINER_STATE["data"] = data
    _POSTGRES_CONTAINER_STATE["state_file"] = state_file
    _POSTGRES_CONTAINER_STATE["lock_path"] = lock_path
    _POSTGRES_CONTAINER_STATE["lock_timeout"] = lock_timeout
    # Build the migrated template on the now-running container (outside
    # the container FileLock; ensure_pg_template holds its own) so the
    # per-test backend fixture clones it instead of re-migrating.
    container_proxy = PostgresContainerProxy(
        host=data["host"],
        port=int(data["port"]),
        username=data["username"],
        password=data["password"],
        dbname=data["dbname"],
    )
    run_pg_template_build(container_proxy, shared_dir)


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainerProxy]:
    """Yield the Postgres container handle pre-acquired in pytest_sessionstart.

    The fixture itself does NO cross-worker coordination -- all of it
    happens in the :func:`pytest_sessionstart` hook so the FileLock
    wait stays out of the per-test 30s ``pytest-timeout`` budget. This
    fixture simply reads the cached state populated by the hook.

    Three modes mirroring the hook's short-circuits:

    * ``"env"``  -- CI ``services: postgres``; yield env-derived proxy.
    * ``"skip"`` -- Docker unavailable or container start failed;
      ``pytest.skip`` with the recorded reason so every test that
      depends on this fixture skips cleanly.
    * ``"container"`` -- local-dev testcontainer is up; yield a proxy
      built from the recorded connection info, and on teardown
      decrement the refcount inside the FileLock (the worker that
      drops to zero stops + removes the container by id).

    The forensic ``sys.stderr.write`` at the top is paired with the one
    in :func:`pytest_sessionstart`; matching the two confirms whether
    the hook ran in the same module instance the fixture reads from.
    """
    _worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    sys.stderr.write(
        f"[conftest:conformance/persistence] postgres_container fixture "
        f"called (worker={_worker}, state_id={id(_POSTGRES_CONTAINER_STATE)}, "
        f"state_keys={sorted(_POSTGRES_CONTAINER_STATE)})\n"
    )
    sys.stderr.flush()
    mode = _POSTGRES_CONTAINER_STATE.get("mode")
    if mode == "env":
        env_proxy = _proxy_from_env()
        assert env_proxy is not None
        yield env_proxy
        return
    if mode == "skip":
        pytest.skip(
            _POSTGRES_CONTAINER_STATE.get(
                "skip_reason", "postgres_container unavailable"
            )
        )
    if mode != "container":
        msg = (
            "postgres_container called before pytest_sessionstart hook ran; "
            "this should be impossible -- check that conftest discovery is "
            "intact."
        )
        raise RuntimeError(msg)

    data = _POSTGRES_CONTAINER_STATE["data"]
    state_file = _POSTGRES_CONTAINER_STATE["state_file"]
    lock_path = _POSTGRES_CONTAINER_STATE["lock_path"]
    lock_timeout = _POSTGRES_CONTAINER_STATE["lock_timeout"]
    proxy = PostgresContainerProxy(
        host=data["host"],
        port=data["port"],
        username=data["username"],
        password=data["password"],
        dbname=data["dbname"],
    )
    try:
        yield proxy
    finally:
        # Teardown lock-wait is on the worker-exit path: the only thing
        # we're contending for is refcount bookkeeping, and a slow wait
        # here doesn't trip any per-test timer because tests have all
        # completed by the time session-scope teardown runs.
        with FileLock(lock_path, timeout=lock_timeout):
            _release_shared_postgres(state_file)


def _container_host_ipv4(container: PostgresContainerProxy) -> str:
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
    container: PostgresContainerProxy,
    db_name: str,
) -> PostgresPersistenceBackend:
    """Create *db_name* by cloning the session-built migrated template.

    Clones the template database (built once at session start by
    :func:`_pre_acquire_postgres_container_state`) instead of replaying
    the migration chain, so per-test setup is a near-instant file copy
    rather than a 7-10s ``migrate()``. On a failed connect the
    half-created database is dropped before re-raising. Kept as a thin
    wrapper around the shared helper because
    ``tests/integration/persistence/test_wp1_restart_safety.py`` imports
    this name directly.
    """
    return await clone_from_template(container, db_name)


async def _drop_postgres_database(
    container: PostgresContainerProxy,
    db_name: str,
) -> None:
    """Terminate remaining sessions on *db_name* and drop it.

    Thin wrapper around the shared helper; kept because
    ``tests/integration/persistence/test_wp1_restart_safety.py`` imports
    this name directly.
    """
    await drop_test_database(container, db_name)


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

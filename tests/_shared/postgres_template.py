"""Session-shared migrated Postgres template for fast per-test DB cloning.

Per-test Postgres fixtures historically ran ``CREATE DATABASE`` +
``connect()`` + ``migrate()`` -- a full yoyo migration-chain replay
costing 7-10s of *setup* per test, across hundreds of ``[postgres]``
tests. That setup dominated the integration job's wall-clock (adding
more xdist workers made it worse, because they contend on the one
shared server replaying migrations concurrently).

This module migrates the schema ONCE per Postgres server (keyed by
``host:port``, coordinated across xdist workers via a ``FileLock``)
into a template database, then per test does ``CREATE DATABASE ...
TEMPLATE <template>`` -- a near-instant file-level copy that preserves
full per-test isolation. It mirrors the SQLite arm's ``_get_template_db``
migrated-file template in ``tests/conftest.py``.

The template is marked ``IS_TEMPLATE true`` + ``ALLOW_CONNECTIONS
false`` after the build so concurrent ``CREATE DATABASE ... TEMPLATE``
clones from multiple workers never trip "source database is being
accessed by other users".
"""

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Final

import psycopg
import psycopg.conninfo
import pytest
from filelock import FileLock
from psycopg import sql
from pydantic import SecretStr

from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from tests._shared.postgres_proxy import PostgresContainerProxy


def xdist_shared_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Return the xdist run-wide temp dir shared across all workers.

    ``getbasetemp()`` is the worker-local ``popen-gwN`` subdir under
    xdist; its ``.parent`` is the run-wide base every worker sees. The
    master (non-xdist) case has no per-worker subdir, so ``getbasetemp()``
    is already the shared root. Used to key the cross-worker template
    sentinel + FileLock.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")
    base = tmp_path_factory.getbasetemp()
    return base if worker_id == "master" else base.parent


TEMPLATE_DB_NAME: Final[str] = "synthorg_pg_template"

# Catastrophe ceiling, not the expected wait. A follower blocks in
# ``acquire()`` only until the leader releases (i.e. the leader's build
# time, typically one migration replay), then re-checks the sentinel and
# skips. 600s only fires if the leader genuinely hangs. Aligned with the
# SQLite ``_get_template_db`` and the conformance container coordinator.
_LOCK_TIMEOUT_SECONDS: Final[int] = 600


def _ipv4(host: str) -> str:
    """Force IPv4 to dodge Docker Desktop's flaky IPv6 port proxy.

    ``get_container_host_ip()`` returns ``"localhost"`` on Docker Desktop;
    Go/psycopg may then prefer ``::1`` whose vpnkit port proxy drops the
    first connection right after container start. Forcing 127.0.0.1
    sidesteps the race (same fix the conformance conftest applies).
    """
    if host in {"localhost", "::1", ""}:
        return "127.0.0.1"
    return host


def _admin_conninfo(proxy: PostgresContainerProxy) -> str:
    """Conninfo for the server's default (admin) database."""
    return psycopg.conninfo.make_conninfo(
        host=_ipv4(proxy.get_container_host_ip()),
        port=int(proxy.get_exposed_port(5432)),
        user=proxy.username,
        password=proxy.password,
        dbname=proxy.dbname,
    )


def _config_for(proxy: PostgresContainerProxy, db_name: str) -> PostgresConfig:
    """Backend config bound to *db_name* on the proxy's server.

    Mirrors the pool/timeout settings the per-test fixtures used before
    this template path existed, so backend behaviour under test is
    unchanged -- only the schema-creation route differs.
    """
    return PostgresConfig(
        host=_ipv4(proxy.get_container_host_ip()),
        port=int(proxy.get_exposed_port(5432)),
        database=db_name,
        username=proxy.username,
        password=SecretStr(proxy.password),
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=4,
        pool_timeout_seconds=10.0,
        connect_timeout_seconds=5.0,
    )


def _server_key(proxy: PostgresContainerProxy) -> str:
    """Stable filename-safe key for the proxy's server (host+port).

    CI runs one shared ``services: postgres`` so every worker resolves
    the same key and builds the template once; local dev gives each
    worker its own testcontainer on a distinct port, so each builds its
    own template -- both correct, no cross-server collision.
    """
    host = _ipv4(proxy.get_container_host_ip()).replace(".", "-").replace(":", "-")
    return f"{host}_{int(proxy.get_exposed_port(5432))}"


async def _drop_database(proxy: PostgresContainerProxy, db_name: str) -> None:
    """Drop *db_name*, force-terminating any remaining sessions.

    ``WITH (FORCE)`` (PG13+) terminates lingering connections atomically,
    avoiding the race between a separate ``pg_terminate_backend`` and the
    drop.
    """
    async with await psycopg.AsyncConnection.connect(
        _admin_conninfo(proxy), autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(db_name)
            )
        )


async def drop_test_database(proxy: PostgresContainerProxy, db_name: str) -> None:
    """Public teardown helper: drop a per-test database created by a clone."""
    await _drop_database(proxy, db_name)


async def _build_template(proxy: PostgresContainerProxy) -> None:
    """Create + migrate the template DB, then seal it as a no-connect template.

    Any partial template left by a crashed prior build is cleared first
    (clearing ``datistemplate`` so a previously-sealed template can be
    dropped). The backend pool is fully disconnected before sealing so
    ``ALLOW_CONNECTIONS false`` has no live sessions to refuse.
    """
    admin = _admin_conninfo(proxy)
    async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
        # A sealed template cannot be dropped until IS_TEMPLATE is cleared.
        # Use the supported DDL (direct pg_database catalog writes need
        # allow_system_table_mods, off by default); ALTER errors if the DB
        # is absent, so gate it on an existence check.
        res = await conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s AND datistemplate = true",
            (TEMPLATE_DB_NAME,),
        )
        if await res.fetchone() is not None:
            await conn.execute(
                sql.SQL("ALTER DATABASE {} WITH IS_TEMPLATE false").format(
                    sql.Identifier(TEMPLATE_DB_NAME)
                )
            )
        await conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(TEMPLATE_DB_NAME)
            )
        )
        await conn.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEMPLATE_DB_NAME))
        )

    backend = PostgresPersistenceBackend(_config_for(proxy, TEMPLATE_DB_NAME))
    try:
        await backend.connect()
        await backend.migrate()
    finally:
        await backend.disconnect()

    async with await psycopg.AsyncConnection.connect(admin, autocommit=True) as conn:
        await conn.execute(
            sql.SQL(
                "ALTER DATABASE {} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false"
            ).format(sql.Identifier(TEMPLATE_DB_NAME))
        )


async def ensure_pg_template(proxy: PostgresContainerProxy, shared_dir: Path) -> str:
    """Ensure the migrated template DB exists on the proxy's server; return its name.

    Idempotent and cross-worker safe: a per-server sentinel file under
    *shared_dir* (the xdist run-wide temp dir) short-circuits once the
    template is built; the first worker to take the ``FileLock`` builds
    it while the rest block on ``acquire()`` until the build completes,
    then skip via the re-checked sentinel.
    """
    key = _server_key(proxy)
    sentinel = shared_dir / f"pg_template_{key}.ready"
    lock_path = shared_dir / f"pg_template_{key}.lock"
    if await asyncio.to_thread(sentinel.exists):
        return TEMPLATE_DB_NAME
    await asyncio.to_thread(shared_dir.mkdir, parents=True, exist_ok=True)

    def _acquire() -> FileLock:
        fl = FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_SECONDS)
        fl.acquire()
        return fl

    lock = await asyncio.to_thread(_acquire)
    try:
        if not await asyncio.to_thread(sentinel.exists):
            await _build_template(proxy)
            await asyncio.to_thread(sentinel.write_text, "ready")
    finally:
        await asyncio.to_thread(lock.release)
    return TEMPLATE_DB_NAME


def run_pg_template_build(proxy: PostgresContainerProxy, shared_dir: Path) -> str:
    """Build the template synchronously under a Selector-pinned loop.

    Session-scope hooks invoke the template build from a bare synchronous
    context. A plain ``asyncio.run`` there constructs Windows' default
    ``ProactorEventLoop``, which psycopg 3's async mode cannot drive -- it
    needs the ``add_reader`` / ``add_writer`` socket polling only a
    select-style loop provides. The pytest-asyncio
    ``pytest_asyncio_loop_factories`` hook pins ``SelectorEventLoop`` for
    test-managed loops, but it does not reach these bare session-hook
    runs, so the loop is pinned explicitly here. Off Windows the default
    loop already polls via select, so ``loop_factory`` stays ``None``.
    """
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(ensure_pg_template(proxy, shared_dir), loop_factory=loop_factory)


async def clone_from_template(
    proxy: PostgresContainerProxy, db_name: str
) -> PostgresPersistenceBackend:
    """Create *db_name* from the migrated template and return a connected backend.

    Replaces the per-test ``CREATE DATABASE`` + ``connect()`` +
    ``migrate()`` sequence: the clone copies the already-migrated
    template's files (near-instant) so no migration chain is replayed.
    The caller owns teardown (``drop_test_database``). On a failed
    connect the half-created database is dropped before re-raising.
    """
    async with await psycopg.AsyncConnection.connect(
        _admin_conninfo(proxy), autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(db_name), sql.Identifier(TEMPLATE_DB_NAME)
            )
        )
    backend = PostgresPersistenceBackend(_config_for(proxy, db_name))
    try:
        await backend.connect()
    except BaseException:

        async def _cleanup() -> None:
            with contextlib.suppress(BaseException):
                await backend.disconnect()
            await _drop_database(proxy, db_name)

        # Shield the best-effort teardown so a cancellation of the calling
        # task cannot interrupt it mid-way and leak the half-created DB,
        # and suppress everything (including CancelledError) so the
        # original connect failure is the exception that propagates.
        with contextlib.suppress(BaseException):
            await asyncio.shield(_cleanup())
        raise
    return backend

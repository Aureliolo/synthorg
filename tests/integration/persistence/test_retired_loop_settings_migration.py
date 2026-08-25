"""The retired-inner-loop settings migration rewrites stored rows.

A settings value is validated on write and never on read, so a row naming
``plan_execute`` or ``hybrid`` outlives the loops themselves and would surface
in the dashboard as a loop that no longer exists.

The revision is exercised by seeding the row *before* it runs: the earlier
revisions are applied from a pruned copy of the revisions directory, the stale
value is written through the repository, and the full directory is then applied
so yoyo runs exactly the one revision left.

Both backends run the whole flow. Byte-identical SQL and a syntax check against
an empty database would leave the ``REPLACE`` / ``LIKE`` behaviour itself
unproven on Postgres, which is the half a rewrite can actually diverge on.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import psycopg.conninfo
import pytest
from psycopg import sql
from pydantic import SecretStr

from synthorg.core.types import NotBlankStr
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.migration_helpers import (
    BackendName,
    copy_revisions,
    revisions_dir,
    to_postgres_url,
    to_sqlite_url,
)
from synthorg.persistence.migrations import migrate_apply
from synthorg.persistence.postgres.backend import PostgresPersistenceBackend
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from tests._shared.postgres_proxy import PostgresContainerProxy

pytestmark = pytest.mark.integration

_REVISION_STEM = "20260806000000_retire_plan_execute_and_hybrid_loops"
_ENGINE = NotBlankStr("engine")
_DEFAULT_LOOP = NotBlankStr("default_loop_type")
_OVERRIDES = NotBlankStr("loop_complexity_overrides")

SeedAndMigrate = Callable[[tuple[SettingRow, ...]], Awaitable[PersistenceBackend]]
"""Seeds settings rows before the revision under test, then applies it."""


def _pruned_revisions(dest: Path, *, backend: BackendName) -> Path:
    """Copy a backend's revisions directory minus the revision under test.

    Args:
        dest: Directory to copy into.
        backend: Which backend's revisions to copy.

    Returns:
        The destination directory, with the revision under test removed.
    """
    copied = copy_revisions(dest, backend=backend)
    (copied / f"{_REVISION_STEM}.sql").unlink()
    return copied


def _row(key: NotBlankStr, value: str) -> SettingRow:
    """Build an engine-namespace settings row carrying *value*.

    Returns:
        The row, stamped with a fixed ``updated_at``.
    """
    return SettingRow(
        namespace=_ENGINE,
        key=key,
        value=value,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    )


async def _seed_and_migrate_sqlite(
    tmp_path: Path,
    rows: tuple[SettingRow, ...],
) -> PersistenceBackend:
    """Apply every earlier revision, write *rows*, then apply the last one.

    Returns:
        A connected backend whose settings have been through the revision.
    """
    db_path = tmp_path / "retired-loops.db"
    db_url = to_sqlite_url(str(db_path))
    pruned = _pruned_revisions(tmp_path / "pruned-revisions", backend="sqlite")

    await migrate_apply(db_url, revisions_path=pruned, backend="sqlite")

    seeding = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await seeding.connect()
    for row in rows:
        await seeding.settings.save(row)
    await seeding.disconnect()

    await migrate_apply(db_url, backend="sqlite")

    migrated = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await migrated.connect()
    return migrated


def _postgres_config(container: PostgresContainerProxy, db_name: str) -> PostgresConfig:
    """Build a config pointing at *db_name* on the shared container.

    Returns:
        The config for a per-test database.
    """
    return PostgresConfig(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        database=db_name,
        username=container.username,
        password=SecretStr(container.password),
        ssl_mode="disable",
        pool_min_size=1,
        pool_max_size=2,
    )


def _admin_conninfo(container: PostgresContainerProxy) -> str:
    """Build the maintenance-database conninfo for CREATE / DROP DATABASE.

    Returns:
        A psycopg conninfo string for the container's default database.
    """
    return psycopg.conninfo.make_conninfo(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        user=container.username,
        password=container.password,
        dbname=container.dbname,
    )


async def _seed_and_migrate_postgres(
    tmp_path: Path,
    rows: tuple[SettingRow, ...],
    container: PostgresContainerProxy,
    db_name: str,
) -> PersistenceBackend:
    """Postgres arm of the seed-then-migrate flow, on a bare database.

    The conformance suite's template clone arrives already fully migrated, so
    it has no seam for writing a row the last revision then rewrites; this
    creates an empty database and replays the chain instead.

    Returns:
        A connected backend whose settings have been through the revision.
    """
    async with await psycopg.AsyncConnection.connect(
        _admin_conninfo(container), autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
        )

    config = _postgres_config(container, db_name)
    db_url = to_postgres_url(config)
    pruned = _pruned_revisions(tmp_path / "pruned-revisions", backend="postgres")

    await migrate_apply(db_url, revisions_path=pruned, backend="postgres")

    seeding = PostgresPersistenceBackend(config)
    await seeding.connect()
    for row in rows:
        await seeding.settings.save(row)
    await seeding.disconnect()

    await migrate_apply(db_url, backend="postgres")

    migrated = PostgresPersistenceBackend(config)
    await migrated.connect()
    return migrated


async def _drop_database(container: PostgresContainerProxy, db_name: str) -> None:
    """Terminate remaining sessions on *db_name* and drop it."""
    async with await psycopg.AsyncConnection.connect(
        _admin_conninfo(container), autocommit=True
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
async def seed_and_migrate(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> AsyncIterator[SeedAndMigrate]:
    """Yield a per-backend seed-then-migrate helper, cleaning up after.

    The helper is a plain async callable returning a backend; the fixture
    tracks every backend it opens and tears them down in its own ``finally``,
    so one is closed (and, on Postgres, its database dropped) even when the
    assertion inside the test fails.
    """
    backends: list[PersistenceBackend] = []
    databases: list[str] = []
    container: PostgresContainerProxy | None = None
    if request.param == "postgres":
        container = request.getfixturevalue("postgres_container")

    async def _seed(rows: tuple[SettingRow, ...]) -> PersistenceBackend:
        if container is None:
            backend = await _seed_and_migrate_sqlite(tmp_path, rows)
        else:
            db_name = f"retired_loops_{uuid4().hex}"
            databases.append(db_name)
            backend = await _seed_and_migrate_postgres(
                tmp_path, rows, container, db_name
            )
        backends.append(backend)
        return backend

    try:
        yield _seed
    finally:
        for backend in backends:
            await backend.disconnect()
        if container is not None:
            for db_name in databases:
                await _drop_database(container, db_name)


class TestRetiredLoopSettingsMigration:
    @pytest.mark.parametrize("retired", ["plan_execute", "hybrid"])
    async def test_a_stored_default_loop_type_is_rewritten_to_react(
        self,
        seed_and_migrate: SeedAndMigrate,
        retired: str,
    ) -> None:
        backend = await seed_and_migrate((_row(_DEFAULT_LOOP, retired),))
        stored = await backend.settings.get((_ENGINE, _DEFAULT_LOOP))
        assert stored is not None
        assert stored.value == "react"

    async def test_overrides_naming_retired_loops_are_rewritten(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        backend = await seed_and_migrate(
            (_row(_OVERRIDES, "medium:plan_execute,complex:hybrid"),),
        )
        stored = await backend.settings.get((_ENGINE, _OVERRIDES))
        assert stored is not None
        assert stored.value == "medium:react,complex:react"

    async def test_a_name_this_revision_does_not_retire_is_left_alone(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        """This revision rewrites its two names and touches nothing else.

        A revision is pinned to the vocabulary it declares, so a name it does
        not list passes through untouched however that name fares elsewhere.
        ``openhands`` is the control precisely because it is outside the two
        this revision retires.
        """
        backend = await seed_and_migrate(
            (
                _row(_DEFAULT_LOOP, "openhands"),
                _row(_OVERRIDES, "epic:openhands,medium:hybrid"),
            ),
        )
        default = await backend.settings.get((_ENGINE, _DEFAULT_LOOP))
        overrides = await backend.settings.get((_ENGINE, _OVERRIDES))
        assert default is not None
        assert default.value == "openhands"
        assert overrides is not None
        assert overrides.value == "epic:openhands,medium:react"

    def test_both_backends_ship_the_same_statements(self) -> None:
        """Dual-backend parity: the rewrite must not diverge by backend."""
        sqlite_sql = (revisions_dir("sqlite") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        postgres_sql = (revisions_dir("postgres") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        assert sqlite_sql == postgres_sql

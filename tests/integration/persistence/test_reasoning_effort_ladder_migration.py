"""The reasoning-effort-ladder repair migration rewrites stored rows.

``engine.reasoning_effort_low``'s registered default moved from ``'none'``
to ``'low'``. A deployment that ran the ``cost_disciplined`` posture has
both ``reasoning_effort_low`` and ``reasoning_effort_normal`` persisted at
``'none'`` (the posture wrote the OLD default explicitly), which would
otherwise strand it on the old behaviour and invert the ladder once
``reasoning_effort_low`` starts reading the new, higher-ranked default
while ``reasoning_effort_normal`` stays behind. The migration deletes the
first row (so it inherits the new default) and rewrites the second back to
its own registered default.

The revision is exercised the same way as the retired-loop-settings
migration: seed the row *before* it runs, against a pruned copy of the
revisions directory, then apply the full chain so yoyo runs exactly the one
revision under test.
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

_REVISION_STEM = "20260831000000_drop_circuit_breaker_state_and_repair_reasoning_effort"
_ENGINE = NotBlankStr("engine")
_LOW = NotBlankStr("reasoning_effort_low")
_NORMAL = NotBlankStr("reasoning_effort_normal")

SeedAndMigrate = Callable[[tuple[SettingRow, ...]], Awaitable[PersistenceBackend]]
"""Seeds settings rows before the revision under test, then applies it."""


def _pruned_revisions(dest: Path, *, backend: BackendName) -> Path:
    """Copy a backend's revisions directory minus the revision under test.

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
    db_path = tmp_path / "reasoning-ladder.db"
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
    """Yield a per-backend seed-then-migrate helper, cleaning up after."""
    backends: list[PersistenceBackend] = []
    databases: list[str] = []
    container: PostgresContainerProxy | None = None
    if request.param == "postgres":
        container = request.getfixturevalue("postgres_container")

    async def _seed(rows: tuple[SettingRow, ...]) -> PersistenceBackend:
        if container is None:
            backend = await _seed_and_migrate_sqlite(tmp_path, rows)
        else:
            db_name = f"reasoning_ladder_{uuid4().hex}"
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


class TestReasoningEffortLadderMigration:
    async def test_a_none_valued_low_row_is_deleted_so_it_inherits_the_new_default(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        backend = await seed_and_migrate((_row(_LOW, "none"),))
        stored = await backend.settings.get((_ENGINE, _LOW))
        assert stored is None

    async def test_a_none_valued_normal_row_is_rewritten_to_low(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        backend = await seed_and_migrate((_row(_NORMAL, "none"),))
        stored = await backend.settings.get((_ENGINE, _NORMAL))
        assert stored is not None
        assert stored.value == "low"

    async def test_the_posture_written_pair_is_fully_repaired(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        backend = await seed_and_migrate(
            (_row(_LOW, "none"), _row(_NORMAL, "none")),
        )
        low = await backend.settings.get((_ENGINE, _LOW))
        normal = await backend.settings.get((_ENGINE, _NORMAL))
        assert low is None
        assert normal is not None
        assert normal.value == "low"

    async def test_a_deliberately_chosen_low_value_is_left_alone(
        self,
        seed_and_migrate: SeedAndMigrate,
    ) -> None:
        """The migration is pinned to ``'none'``.

        Any other stored value, deliberately written by an operator,
        passes through untouched.
        """
        backend = await seed_and_migrate(
            (_row(_LOW, "medium"), _row(_NORMAL, "minimal")),
        )
        low = await backend.settings.get((_ENGINE, _LOW))
        normal = await backend.settings.get((_ENGINE, _NORMAL))
        assert low is not None
        assert low.value == "medium"
        assert normal is not None
        assert normal.value == "minimal"

    def test_both_backends_ship_the_same_statements(self) -> None:
        """Dual-backend parity: the rewrite must not diverge by backend."""
        sqlite_sql = (revisions_dir("sqlite") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        postgres_sql = (revisions_dir("postgres") / f"{_REVISION_STEM}.sql").read_text(
            encoding="utf-8"
        )
        assert sqlite_sql == postgres_sql

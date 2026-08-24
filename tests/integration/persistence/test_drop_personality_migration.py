"""The personality row rewrites, on both arms.

A rewrite is exactly the half of a migration that can diverge by backend, and
these two are written in different dialects on purpose: SQLite reaches for
``JSON_REMOVE`` over ``JSON_EACH`` (where the element arrives SQL-typed, so a
text element is not valid JSON and both ``JSON_REMOVE`` and ``JSON_TYPE``
reject it), Postgres for ``jsonb - text`` over ``JSONB_ARRAY_ELEMENTS`` (where
the operator raises on a scalar operand and the WHERE clause has no evaluation
order to rely on). Asserting only one arm leaves the other's guards unproven,
and each guard exists because the unguarded form takes the whole revision down
on one hand-edited row.

Seeded through raw SQL for the archive: the shapes under test are precisely the
ones the reader now refuses, so no writer can produce them.
"""

import json
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import psycopg
import psycopg.conninfo
import pytest
from psycopg import sql
from pydantic import SecretStr

from synthorg.config.agent_schema import AgentConfig
from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.persistence.config import PostgresConfig, SQLiteConfig
from synthorg.persistence.migration_helpers import (
    BackendName,
    copy_revisions,
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

_REVISION_STEM = "20260824150000_drop_personality_surface"
_COMPANY = NotBlankStr("company")
_AGENTS = NotBlankStr("agents")
_STAMP = datetime(2026, 8, 1, 9, 0, tzinfo=UTC).isoformat()

_INSERT_VERSION = (
    "INSERT INTO agent_identity_versions "
    "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
    "VALUES ({p}, {p}, {p}, {p}, {p}, {p})"
)
_SELECT_VERSION = "SELECT snapshot FROM agent_identity_versions WHERE entity_id = {p}"

_MODEL: dict[str, object] = {
    "provider": "test-provider",
    "model_id": "test-capable-001",
}

#: The personality block a preset expanded to, in full: the five scored floats,
#: the six behavioural enums and the three free-text fields.
_PERSONALITY: dict[str, object] = {
    "traits": ["analytical", "pragmatic"],
    "communication_style": "concise and technical",
    "risk_tolerance": "low",
    "creativity": "medium",
    "description": "A methodical analyst.",
    "openness": 0.6,
    "conscientiousness": 0.9,
    "extraversion": 0.3,
    "agreeableness": 0.5,
    "stress_response": 0.7,
    "decision_making": "analytical",
    "collaboration": "pair",
    "verbosity": "balanced",
    "conflict_approach": "compromise",
}

_STALE_SNAPSHOT: dict[str, object] = {
    "name": "Ada Chen",
    "role": "Developer",
    "department": "engineering",
    "hiring_date": "2026-01-01",
    "model": _MODEL,
    "personality": _PERSONALITY,
}

#: The roster as an installation held it: an agent carrying the expanded
#: personality block, one carrying only the preset name, and beside them an
#: element that is not an object at all. The last is what forces Postgres to
#: remove the keys per element, since ``jsonb - text`` raises on a scalar and
#: one hand-edited row would otherwise roll the whole revision back.
_STALE_ROSTER: list[object] = [
    {
        "name": "Ada Chen",
        "role": "Developer",
        "department": "engineering",
        "model": _MODEL,
        "personality": _PERSONALITY,
        "personality_preset": "methodical_analyst",
    },
    {
        "name": "Grace Bell",
        "role": "CEO",
        "department": "executive",
        "model": _MODEL,
        "personality_preset": "visionary_leader",
    },
    "hand-edited",
]


class _Archive(Protocol):
    """Raw access to the identity archive, in one backend's dialect."""

    async def seed(self, entity_id: str, snapshot: str) -> None:
        """Write one pre-removal snapshot."""
        ...

    async def read(self, entity_id: str) -> dict[str, object]:
        """Read one snapshot back.

        Returns:
            The stored snapshot, parsed.
        """
        ...


def _parsed(raw: object) -> dict[str, object]:
    """Parse a stored snapshot, however its driver handed it over.

    SQLite stores the archive as TEXT and returns the text; Postgres stores it
    as ``JSONB`` and psycopg returns it already parsed. Stringifying the second
    yields a Python repr rather than JSON, so the shape is decided here.

    Returns:
        The snapshot as a mapping.
    """
    loaded: object = json.loads(raw) if isinstance(raw, str | bytes) else raw
    assert isinstance(loaded, dict)
    return cast("dict[str, object]", loaded)


class _SqliteArchive:
    """The SQLite arm's archive access."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def seed(self, entity_id: str, snapshot: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                _INSERT_VERSION.format(p="?"),
                (entity_id, 1, f"hash-{entity_id}", snapshot, "operator", _STAMP),
            )

    async def read(self, entity_id: str) -> dict[str, object]:
        """Read one snapshot back.

        Returns:
            The stored snapshot, parsed.
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(_SELECT_VERSION.format(p="?"), (entity_id,)).fetchone()
        assert row is not None
        return _parsed(row[0])


class _PostgresArchive:
    """The Postgres arm's archive access."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo

    async def seed(self, entity_id: str, snapshot: str) -> None:
        async with await psycopg.AsyncConnection.connect(
            self._conninfo, autocommit=True
        ) as conn:
            await conn.execute(
                _INSERT_VERSION.format(p="%s"),
                (entity_id, 1, f"hash-{entity_id}", snapshot, "operator", _STAMP),
            )

    async def read(self, entity_id: str) -> dict[str, object]:
        """Read one snapshot back.

        Returns:
            The stored snapshot, parsed.
        """
        async with await psycopg.AsyncConnection.connect(self._conninfo) as conn:
            cursor = await conn.execute(_SELECT_VERSION.format(p="%s"), (entity_id,))
            row = await cursor.fetchone()
        assert row is not None
        return _parsed(row[0])


class _Arm(Protocol):
    """One migrated backend, and the archive beside it."""

    backend: PersistenceBackend
    archive: _Archive


class _MigratedArm:
    """What a seeded, migrated backend hands the assertions."""

    def __init__(self, backend: PersistenceBackend, archive: _Archive) -> None:
        self.backend = backend
        self.archive = archive


SeedAndMigrate = Callable[[], Awaitable[_Arm]]
"""Seeds the pre-removal shapes, then applies the revision under test."""


def _pruned_revisions(dest: Path, *, backend: BackendName) -> Path:
    """Copy a backend's revisions minus the revision under test.

    Returns:
        The destination directory.
    """
    copied = copy_revisions(dest, backend=backend)
    (copied / f"{_REVISION_STEM}.sql").unlink()
    return copied


def _roster_row() -> SettingRow:
    """Build the stored roster carrying the personality keys.

    Returns:
        The ``company.agents`` row as an installation held it.
    """
    return SettingRow(
        namespace=_COMPANY,
        key=_AGENTS,
        value=json.dumps(_STALE_ROSTER),
        updated_at=_STAMP,
    )


async def _seed_sqlite(tmp_path: Path) -> _MigratedArm:
    """Apply every earlier revision, seed the stale shapes, then migrate.

    Returns:
        The migrated backend and its archive.
    """
    db_path = tmp_path / "drop-personality.db"
    db_url = to_sqlite_url(str(db_path))
    pruned = _pruned_revisions(tmp_path / "pruned-revisions", backend="sqlite")
    await migrate_apply(db_url, revisions_path=pruned, backend="sqlite")

    archive = _SqliteArchive(db_path)
    seeding = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await seeding.connect()
    await seeding.settings.save(_roster_row())
    await seeding.disconnect()
    await archive.seed("agent-1", json.dumps(_STALE_SNAPSHOT))

    await migrate_apply(db_url, backend="sqlite")
    migrated = SQLitePersistenceBackend(SQLiteConfig(path=str(db_path)))
    await migrated.connect()
    return _MigratedArm(migrated, archive)


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


def _conninfo(container: PostgresContainerProxy, db_name: str) -> str:
    """Build a conninfo string for *db_name* on the container.

    Returns:
        The psycopg conninfo string.
    """
    return psycopg.conninfo.make_conninfo(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        user=container.username,
        password=container.password,
        dbname=db_name,
    )


async def _seed_postgres(
    tmp_path: Path, container: PostgresContainerProxy, db_name: str
) -> _MigratedArm:
    """Postgres arm of the seed-then-migrate flow, on a bare database.

    Returns:
        The migrated backend and its archive.
    """
    async with await psycopg.AsyncConnection.connect(
        _conninfo(container, container.dbname), autocommit=True
    ) as admin:
        await admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
        )

    config = _postgres_config(container, db_name)
    db_url = to_postgres_url(config)
    pruned = _pruned_revisions(tmp_path / "pruned-revisions", backend="postgres")
    await migrate_apply(db_url, revisions_path=pruned, backend="postgres")

    archive = _PostgresArchive(_conninfo(container, db_name))
    seeding = PostgresPersistenceBackend(config)
    await seeding.connect()
    await seeding.settings.save(_roster_row())
    await seeding.disconnect()
    await archive.seed("agent-1", json.dumps(_STALE_SNAPSHOT))

    await migrate_apply(db_url, backend="postgres")
    migrated = PostgresPersistenceBackend(config)
    await migrated.connect()
    return _MigratedArm(migrated, archive)


async def _drop_database(container: PostgresContainerProxy, db_name: str) -> None:
    """Terminate remaining sessions on *db_name* and drop it."""
    async with await psycopg.AsyncConnection.connect(
        _conninfo(container, container.dbname), autocommit=True
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

    The fixture tracks every backend it opens and tears them down in its own
    ``finally``, so one is closed (and, on Postgres, its database dropped)
    even when the assertion inside the test fails.
    """
    arms: list[_MigratedArm] = []
    databases: list[str] = []
    container: PostgresContainerProxy | None = None
    if request.param == "postgres":
        container = request.getfixturevalue("postgres_container")

    async def _seed() -> _Arm:
        if container is None:
            arm = await _seed_sqlite(tmp_path)
        else:
            db_name = f"drop_personality_{uuid4().hex}"
            databases.append(db_name)
            arm = await _seed_postgres(tmp_path, container, db_name)
        arms.append(arm)
        return arm

    try:
        yield _seed
    finally:
        for arm in arms:
            await arm.backend.disconnect()
        if container is not None:
            for db_name in databases:
                await _drop_database(container, db_name)


def _roster_of(stored: SettingRow | None) -> list[object]:
    """Parse the stored roster back into its array.

    Returns:
        The ``company.agents`` array as stored.
    """
    assert stored is not None
    parsed: object = json.loads(stored.value)
    assert isinstance(parsed, list)
    return cast("list[object]", parsed)


class TestTheIdentityArchive:
    async def test_the_personality_key_is_gone(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        arm = await seed_and_migrate()

        assert "personality" not in await arm.archive.read("agent-1")

    async def test_the_snapshot_reads_as_the_model_it_is_stored_as(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """The defect is not the key: it is every snapshot being unreadable.

        ``AgentIdentity`` forbids extras, so one retired key makes the whole
        archive fail validation and every read degrade to a drift warning.
        """
        arm = await seed_and_migrate()

        identity = AgentIdentity.model_validate(await arm.archive.read("agent-1"))

        assert str(identity.name) == "Ada Chen"


class TestTheRoster:
    async def test_both_personality_keys_are_dropped_from_every_element(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        for element in roster:
            if isinstance(element, dict):
                assert "personality" not in element
                assert "personality_preset" not in element

    async def test_the_whole_roster_reads_back(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """One stale key empties the company, so every agent element is read."""
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        agents = [AgentConfig.model_validate(e) for e in roster if isinstance(e, dict)]
        assert [str(agent.name) for agent in agents] == ["Ada Chen", "Grace Bell"]

    async def test_a_hand_edited_element_survives_the_rewrite(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """The scalar element is the one that takes the revision down unguarded."""
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        assert roster[-1] == "hand-edited"

    async def test_the_surviving_fields_are_untouched(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        first = roster[0]
        assert isinstance(first, dict)
        assert first["role"] == "Developer"
        assert first["model"] == _MODEL

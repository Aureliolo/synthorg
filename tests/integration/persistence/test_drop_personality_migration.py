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
_INSERT_SETTING = (
    "INSERT INTO settings (namespace, key, value, updated_at) "
    "VALUES ({p}, {p}, {p}, {p})"
)
_SELECT_SETTING = "SELECT value FROM settings WHERE namespace = {p} AND key = {p}"
_INSERT_CHECKPOINT = (
    "INSERT INTO checkpoints "
    "(id, execution_id, agent_id, task_id, turn_number, context_json, created_at) "
    "VALUES ({p}, 'exec-1', 'agent-1', 'task-1', 2, {p}, {p})"
)
_INSERT_PARKED = (
    "INSERT INTO parked_contexts "
    "(id, execution_id, agent_id, task_id, approval_id, parked_at, context_json) "
    "VALUES ({p}, 'exec-1', 'agent-1', 'task-1', 'appr-1', {p}, {p})"
)
_SELECT_CONTEXTS = "SELECT id, context_json FROM {table}"

#: Both context tables, which the revision rewrites with the same statement.
CONTEXT_TABLES = ("checkpoints", "parked_contexts")


def _identity_of(row_id: object, context_json: object) -> tuple[str, object]:
    """Pull the ``identity`` member out of one stored context row.

    Returns:
        The row id and the identity its context carries.
    """
    loaded: object = (
        json.loads(context_json)
        if isinstance(context_json, str | bytes)
        else context_json
    )
    assert isinstance(loaded, dict)
    return str(row_id), cast("dict[str, object]", loaded).get("identity")


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

_CLEAN_SNAPSHOT: dict[str, object] = {
    "name": "Ida Ross",
    "role": "Completion Reviewer",
    "department": "quality_assurance",
    "hiring_date": "2026-01-01",
    "model": _MODEL,
}

#: The roster as an installation held it: an agent carrying the expanded
#: personality block, one whose ``personality`` is an explicit JSON null (which
#: no ``json_extract`` can tell from a missing key), one carrying only the
#: preset name, and beside them every JSON type that is not an object. Those
#: last force Postgres to remove the keys per element, since ``jsonb - text``
#: raises on a scalar and one hand-edited row would otherwise roll the whole
#: revision back; on SQLite each takes a different branch of the CASE, because
#: ``json_each`` hands an element back SQL-typed rather than as JSON text.
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
        "personality": None,
        "personality_preset": "visionary_leader",
    },
    "hand-edited",
    True,
    False,
    3,
    [1, 2],
    None,
]

#: Every element of the roster that is not an object, in order. The rewrite has
#: to hand each back as itself: SQLite's ``JSON_QUOTE`` would rewrite a boolean
#: as a number without its own CASE branch.
_NON_OBJECT_ELEMENTS: list[object] = ["hand-edited", True, False, 3, [1, 2], None]

#: Settings rows whose value is not JSON. ``settings`` is one table for every
#: namespace, and Postgres does not promise to evaluate a WHERE clause left to
#: right, so the roster rewrite must reach its own row without ever casting
#: these: a Fernet blob and a bare enum name are what actually sit beside it.
_NON_JSON_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("providers", "configs", "gAAAAABm-not-valid-json-ciphertext=="),
    ("security", "prompt_injection_action", "log_only"),
)


def _context(identity: dict[str, object]) -> str:
    """Serialise an ``AgentContext``-shaped row around *identity*.

    Returns:
        The ``context_json`` an execution would have persisted.
    """
    return json.dumps(
        {
            "execution_id": "exec-1",
            "identity": identity,
            "turn_count": 2,
            "max_turns": 40,
        }
    )


class _Archive(Protocol):
    """Raw access to the rows the revision rewrites, in a backend's dialect.

    Raw rather than through a repository, because the shapes under test are
    precisely the ones the readers now refuse, so no writer can produce them.
    """

    async def seed(self, entity_id: str, snapshot: str) -> None:
        """Write one pre-removal snapshot."""
        ...

    async def read(self, entity_id: str) -> dict[str, object]:
        """Read one snapshot back.

        Returns:
            The stored snapshot, parsed.
        """
        ...

    async def seed_raw_snapshot(self, entity_id: str, snapshot: str) -> None:
        """Write a snapshot that is not an object at all."""
        ...

    async def raw_snapshot(self, entity_id: str) -> object:
        """Read one snapshot back without assuming it is an object.

        Returns:
            The stored snapshot, parsed.
        """
        ...

    async def seed_contexts(self, rows: dict[str, str]) -> None:
        """Write one checkpoint and one parked context per *rows* entry."""
        ...

    async def contexts(self, table: str) -> dict[str, object]:
        """Read every stored context's ``identity`` member back.

        Returns:
            Row id mapped to the identity its stored context carries.
        """
        ...

    async def seed_settings(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        """Write settings rows whose values are not JSON."""
        ...

    async def settings(self, namespace: str, key: str) -> str:
        """Read one settings value back as stored.

        Returns:
            The stored value.
        """
        ...


def _parsed(raw: object) -> dict[str, object]:
    """Narrow a snapshot each arm has already parsed down to a mapping.

    Both ``raw_snapshot`` implementations answer a parsed value, each in the
    way its own column type requires, so the only work left is the shape.

    Returns:
        The snapshot as a mapping.
    """
    assert isinstance(raw, dict)
    return cast("dict[str, object]", raw)


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
        return _parsed(await self.raw_snapshot(entity_id))

    async def seed_raw_snapshot(self, entity_id: str, snapshot: str) -> None:
        await self.seed(entity_id, snapshot)

    async def raw_snapshot(self, entity_id: str) -> object:
        """Read one snapshot back without assuming it is an object.

        The column is TEXT here, so the driver hands back the stored JSON
        text and this side always parses.

        Returns:
            The stored snapshot, parsed.
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(_SELECT_VERSION.format(p="?"), (entity_id,)).fetchone()
        assert row is not None
        return json.loads(row[0])

    async def seed_contexts(self, rows: dict[str, str]) -> None:
        with sqlite3.connect(self._db_path) as conn:
            for row_id, context_json in rows.items():
                conn.execute(
                    _INSERT_CHECKPOINT.format(p="?"), (row_id, context_json, _STAMP)
                )
                conn.execute(
                    _INSERT_PARKED.format(p="?"), (row_id, _STAMP, context_json)
                )

    async def contexts(self, table: str) -> dict[str, object]:
        """Read every stored context's ``identity`` member back.

        Returns:
            Row id mapped to the identity its stored context carries.
        """
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(_SELECT_CONTEXTS.format(table=table)).fetchall()
        return dict(_identity_of(row_id, ctx) for row_id, ctx in rows)

    async def seed_settings(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        with sqlite3.connect(self._db_path) as conn:
            for namespace, key, value in rows:
                conn.execute(
                    _INSERT_SETTING.format(p="?"), (namespace, key, value, _STAMP)
                )

    async def settings(self, namespace: str, key: str) -> str:
        """Read one settings value back as stored.

        Returns:
            The stored value.
        """
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                _SELECT_SETTING.format(p="?"), (namespace, key)
            ).fetchone()
        assert row is not None
        return str(row[0])


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
        return _parsed(await self.raw_snapshot(entity_id))

    async def seed_raw_snapshot(self, entity_id: str, snapshot: str) -> None:
        await self.seed(entity_id, snapshot)

    async def raw_snapshot(self, entity_id: str) -> object:
        """Read one snapshot back without assuming it is an object.

        The column is JSONB here, so psycopg has already deserialised it and
        this side must NOT parse again: a stored JSON string arrives as a bare
        Python ``str``, and ``json.loads`` on its contents raises.

        Returns:
            The stored snapshot, parsed.
        """
        async with await psycopg.AsyncConnection.connect(self._conninfo) as conn:
            cursor = await conn.execute(_SELECT_VERSION.format(p="%s"), (entity_id,))
            row = await cursor.fetchone()
        assert row is not None
        return row[0]

    async def seed_contexts(self, rows: dict[str, str]) -> None:
        async with await psycopg.AsyncConnection.connect(
            self._conninfo, autocommit=True
        ) as conn:
            for row_id, context_json in rows.items():
                await conn.execute(
                    _INSERT_CHECKPOINT.format(p="%s"), (row_id, context_json, _STAMP)
                )
                await conn.execute(
                    _INSERT_PARKED.format(p="%s"), (row_id, _STAMP, context_json)
                )

    async def contexts(self, table: str) -> dict[str, object]:
        """Read every stored context's ``identity`` member back.

        Returns:
            Row id mapped to the identity its stored context carries.
        """
        async with await psycopg.AsyncConnection.connect(self._conninfo) as conn:
            cursor = await conn.execute(
                sql.SQL("SELECT id, context_json FROM {}").format(sql.Identifier(table))
            )
            rows = await cursor.fetchall()
        return dict(_identity_of(row_id, ctx) for row_id, ctx in rows)

    async def seed_settings(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        async with await psycopg.AsyncConnection.connect(
            self._conninfo, autocommit=True
        ) as conn:
            for namespace, key, value in rows:
                await conn.execute(
                    _INSERT_SETTING.format(p="%s"), (namespace, key, value, _STAMP)
                )

    async def settings(self, namespace: str, key: str) -> str:
        """Read one settings value back as stored.

        Returns:
            The stored value.
        """
        async with await psycopg.AsyncConnection.connect(self._conninfo) as conn:
            cursor = await conn.execute(
                _SELECT_SETTING.format(p="%s"), (namespace, key)
            )
            row = await cursor.fetchone()
        assert row is not None
        return str(row[0])


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


async def _seed_raw(archive: _Archive) -> None:
    """Seed every shape no writer can produce, in one backend's dialect.

    The identity archive and both context tables hold shapes the readers now
    refuse, and the non-JSON settings rows are written raw because the settings
    repository would encrypt or re-encode what it is handed.
    """
    await archive.seed("agent-1", json.dumps(_STALE_SNAPSHOT))
    await archive.seed_raw_snapshot("scalar-snapshot", '"personality"')
    await archive.seed_contexts(
        {
            "ctx-with": _context(_STALE_SNAPSHOT),
            "ctx-without": _context(_CLEAN_SNAPSHOT),
            "ctx-no-identity": json.dumps({"execution_id": "exec-1"}),
        }
    )
    await archive.seed_settings(_NON_JSON_SETTINGS)


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
    await _seed_raw(archive)

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
    await _seed_raw(archive)

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

    async def test_the_surviving_fields_are_untouched(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        first = roster[0]
        assert isinstance(first, dict)
        assert first["role"] == "Developer"
        assert first["model"] == _MODEL

    async def test_an_explicit_json_null_is_removed_not_skipped(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """``json_extract`` cannot tell a JSON null from a missing key.

        A guard written with it would leave this element carrying the key and
        empty the roster, which is the failure the rewrite exists to prevent.
        """
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        grace = next(
            e for e in roster if isinstance(e, dict) and e["name"] == "Grace Bell"
        )
        assert "personality" not in grace

    async def test_every_non_object_element_survives_as_itself(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """Each of these takes the revision down under a different guard.

        On Postgres the scalar is the one `jsonb - text` refuses, rolling the
        whole upgrade back. On SQLite each type takes its own CASE branch, and
        a boolean is the one `JSON_QUOTE` would silently rewrite as a number.
        """
        arm = await seed_and_migrate()

        roster = _roster_of(await arm.backend.settings.get((_COMPANY, _AGENTS)))

        assert [e for e in roster if not isinstance(e, dict)] == _NON_OBJECT_ELEMENTS


class TestRowsTheRewriteMustNotTouch:
    """``settings`` holds every namespace, and only one row of it is JSON.

    Postgres has no evaluation order to rely on, so a guard naming the row
    rather than its shape still reaches the cast on the row it targets. These
    values are what actually sits beside the roster; casting either aborts the
    upgrade with a 22P02 that names a cast rather than a row.
    """

    @pytest.mark.parametrize(("namespace", "key", "value"), _NON_JSON_SETTINGS)
    async def test_a_non_json_settings_row_is_untouched(
        self,
        seed_and_migrate: SeedAndMigrate,
        namespace: str,
        key: str,
        value: str,
    ) -> None:
        arm = await seed_and_migrate()

        assert await arm.archive.settings(namespace, key) == value

    async def test_a_scalar_snapshot_survives_as_itself(
        self, seed_and_migrate: SeedAndMigrate
    ) -> None:
        """``jsonb ? text`` is containment, not a key test.

        On the scalar ``"personality"`` it answers true, and ``snapshot -
        'personality'`` then raises "cannot delete from scalar" and rolls the
        revision back. Only the type guard beside it refuses this row.
        """
        arm = await seed_and_migrate()

        assert await arm.archive.raw_snapshot("scalar-snapshot") == "personality"


class TestInFlightContexts:
    """Both tables embed an identity, and both outlive a restart by design.

    A parked context waits on a human with no deadline and a checkpoint is what
    crash recovery replays, so an upgrade lands between the write and the read.
    Neither reader degrades: they validate into ``AgentContext`` and raise, and
    the parked one preserves the row afterwards, so a resume that cannot parse
    fails identically for ever with the approval already decided.
    """

    @pytest.mark.parametrize("table", CONTEXT_TABLES)
    async def test_every_stored_context_reads_as_the_identity_it_holds(
        self, seed_and_migrate: SeedAndMigrate, table: str
    ) -> None:
        arm = await seed_and_migrate()

        stored = await arm.archive.contexts(table)

        with_key = AgentIdentity.model_validate(stored["ctx-with"])
        without_key = AgentIdentity.model_validate(stored["ctx-without"])

        assert str(with_key.name) == "Ada Chen"
        assert str(without_key.name) == "Ida Ross"

    @pytest.mark.parametrize("table", CONTEXT_TABLES)
    async def test_a_context_with_no_identity_survives_as_itself(
        self, seed_and_migrate: SeedAndMigrate, table: str
    ) -> None:
        """``#-`` raises on a scalar exactly as ``-`` does, so both levels type."""
        arm = await seed_and_migrate()

        stored = await arm.archive.contexts(table)

        assert stored["ctx-no-identity"] is None

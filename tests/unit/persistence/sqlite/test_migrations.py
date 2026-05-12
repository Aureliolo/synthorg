"""Schema constraint tests against the migrated SQLite template database.

The ``migrated_db`` fixture (defined in ``tests/conftest.py``) gives us
a fresh aiosqlite connection backed by a snapshot of the canonical
schema applied via ``synthorg.persistence.migrations`` (yoyo).  Tests
here lock in invariants that must hold whenever the schema is
applied: nullability, composite keys, FK actions, CHECK constraints,
and presence of every expected table.

Migration-engine behaviour (apply / status / baseline / rollback /
URL building / discovery) lives in
``tests/unit/persistence/test_migrations.py``.
"""

import sqlite3

import aiosqlite
import pytest


@pytest.mark.unit
class TestSchemaConstraints:
    """Constraint enforcement tests against the migrated template DB."""

    async def test_parked_contexts_task_id_is_nullable(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """parked_contexts.task_id allows NULL."""
        cursor = await migrated_db.execute("PRAGMA table_info('parked_contexts')")
        columns = {row[1]: row[3] for row in await cursor.fetchall()}
        assert columns["task_id"] == 0

    async def test_settings_has_composite_key(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """settings table has namespace + key as composite primary key."""
        cursor = await migrated_db.execute("PRAGMA table_info('settings')")
        rows = await cursor.fetchall()
        columns = {row[1] for row in rows}
        assert {"namespace", "key", "value", "updated_at"} == columns
        pk_columns = {row[1]: row[5] for row in rows}
        assert pk_columns["namespace"] == 1
        assert pk_columns["key"] == 2

    async def test_decision_records_enforces_audit_constraints(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """decision_records enforces no-self-review and RESTRICT."""
        cursor = await migrated_db.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='decision_records'"
        )
        row = await cursor.fetchone()
        assert row is not None
        ddl = row[0]
        assert "reviewer_agent_id" in ddl
        assert "executing_agent_id" in ddl
        assert "RESTRICT" in ddl

        fk_cursor = await migrated_db.execute(
            "PRAGMA foreign_key_list('decision_records')"
        )
        fks = await fk_cursor.fetchall()
        task_fks = [fk for fk in fks if fk[2] == "tasks" and fk[3] == "task_id"]
        assert len(task_fks) == 1
        assert task_fks[0][6] == "RESTRICT"

    async def test_agent_states_rejects_invalid_status(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """CHECK constraint rejects invalid status values."""
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            await migrated_db.execute(
                "INSERT INTO agent_states "
                "(agent_id, status, last_activity_at) "
                "VALUES (?, ?, ?)",
                ("a", "invalid", "2026-01-01T00:00:00+00:00"),
            )

    async def test_ontology_tables_present(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        """Ontology tables are included in the consolidated schema."""
        cursor = await migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('entity_definitions', 'entity_definition_versions')"
        )
        tables = {row[0] for row in await cursor.fetchall()}
        assert tables == {"entity_definitions", "entity_definition_versions"}

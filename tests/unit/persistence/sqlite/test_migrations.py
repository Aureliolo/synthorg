"""Tests for Atlas-based schema migrations."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.persistence_errors import MigrationError
from synthorg.persistence import atlas


@pytest.mark.unit
class TestMigrateApply:
    """Tests for atlas.migrate_apply()."""

    async def test_applies_to_fresh_db(self, tmp_path: Path) -> None:
        """Baseline migration creates all expected tables."""
        db_path = tmp_path / "fresh.db"
        rev_url = atlas.copy_revisions(tmp_path / "revisions")
        result = await atlas.migrate_apply(
            atlas.to_sqlite_url(str(db_path)),
            revisions_url=rev_url,
            skip_lock=True,
        )

        assert result.applied_count >= 1

        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE 'atlas_%' "
                "ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        assert "tasks" in tables
        assert "users" in tables
        assert "entity_definitions" in tables
        assert "entity_definition_versions" in tables

    async def test_idempotent(self, tmp_path: Path) -> None:
        """Applying migrations twice does not raise."""
        db_path = tmp_path / "idem.db"
        db_url = atlas.to_sqlite_url(str(db_path))
        rev_url = atlas.copy_revisions(tmp_path / "revisions")
        await atlas.migrate_apply(
            db_url,
            revisions_url=rev_url,
            skip_lock=True,
        )
        result = await atlas.migrate_apply(
            db_url,
            revisions_url=rev_url,
            skip_lock=True,
        )

        assert result.applied_count == 0

    async def test_atlas_not_found_raises(self) -> None:
        """MigrationError raised when Atlas binary is missing."""
        with (
            patch("synthorg.persistence.atlas.shutil.which", return_value=None),
            pytest.raises(MigrationError, match="Atlas CLI not found"),
        ):
            await atlas.migrate_apply("sqlite:///tmp/test.db")


@pytest.mark.unit
class TestSchemaConstraints:
    """Constraint enforcement tests using Atlas-migrated database."""

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

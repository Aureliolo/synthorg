"""Unit tests for SQLiteSettingsRepository."""

import aiosqlite
import pytest

from synthorg.persistence.sqlite.settings_repo import SQLiteSettingsRepository
from tests._shared.persistence import make_private_write_context


@pytest.fixture
def repo(migrated_db: aiosqlite.Connection) -> SQLiteSettingsRepository:
    """Settings repo backed by the shared migrated_db fixture."""
    return SQLiteSettingsRepository(
        migrated_db, write_context=make_private_write_context()
    )


@pytest.mark.unit
class TestSQLiteSettingsRepository:
    """Tests for namespaced settings CRUD."""

    async def test_get_returns_none_for_missing(
        self, repo: SQLiteSettingsRepository
    ) -> None:
        result = await repo.get("budget", "nonexistent")
        assert result is None

    async def test_set_and_get(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "total_monthly", "200.0", "2026-03-16T10:00:00Z")
        result = await repo.get("budget", "total_monthly")
        assert result is not None
        value, updated_at = result
        assert value == "200.0"
        assert updated_at == "2026-03-16T10:00:00Z"

    async def test_set_upserts(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z")
        await repo.set("budget", "total_monthly", "300.0", "2026-03-16T11:00:00Z")
        result = await repo.get("budget", "total_monthly")
        assert result is not None
        assert result[0] == "300.0"
        assert result[1] == "2026-03-16T11:00:00Z"

    async def test_delete_existing(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z")
        deleted = await repo.delete("budget", "total_monthly")
        assert deleted is True
        assert await repo.get("budget", "total_monthly") is None

    async def test_delete_nonexistent(self, repo: SQLiteSettingsRepository) -> None:
        deleted = await repo.delete("budget", "nonexistent")
        assert deleted is False

    async def test_get_namespace(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "b_key", "1", "2026-03-16T10:00:00Z")
        await repo.set("budget", "a_key", "2", "2026-03-16T10:00:00Z")
        await repo.set("security", "enabled", "true", "2026-03-16T10:00:00Z")
        result = await repo.get_namespace("budget")
        assert len(result) == 2
        # Sorted by key
        assert result[0] == ("a_key", "2", "2026-03-16T10:00:00Z")
        assert result[1] == ("b_key", "1", "2026-03-16T10:00:00Z")

    async def test_get_namespace_empty(self, repo: SQLiteSettingsRepository) -> None:
        result = await repo.get_namespace("nonexistent")
        assert result == ()

    async def test_get_all(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z")
        await repo.set("security", "enabled", "true", "2026-03-16T10:00:00Z")
        result = await repo.get_all()
        assert len(result) == 2
        # Sorted by (namespace, key)
        assert result[0][0] == "budget"
        assert result[1][0] == "security"

    async def test_get_all_empty(self, repo: SQLiteSettingsRepository) -> None:
        result = await repo.get_all()
        assert result == ()

    async def test_delete_namespace(self, repo: SQLiteSettingsRepository) -> None:
        await repo.set("budget", "a", "1", "2026-03-16T10:00:00Z")
        await repo.set("budget", "b", "2", "2026-03-16T10:00:00Z")
        await repo.set("security", "c", "3", "2026-03-16T10:00:00Z")
        count = await repo.delete_namespace("budget")
        assert count == 2
        assert await repo.get_namespace("budget") == ()
        # security remains
        assert len(await repo.get_namespace("security")) == 1

    async def test_delete_namespace_empty(self, repo: SQLiteSettingsRepository) -> None:
        count = await repo.delete_namespace("nonexistent")
        assert count == 0

    async def test_namespaces_are_isolated(
        self, repo: SQLiteSettingsRepository
    ) -> None:
        """Same key in different namespaces should be independent."""
        await repo.set("budget", "enabled", "false", "2026-03-16T10:00:00Z")
        await repo.set("security", "enabled", "true", "2026-03-16T10:00:00Z")
        budget_val = await repo.get("budget", "enabled")
        security_val = await repo.get("security", "enabled")
        assert budget_val is not None
        assert security_val is not None
        assert budget_val[0] == "false"
        assert security_val[0] == "true"

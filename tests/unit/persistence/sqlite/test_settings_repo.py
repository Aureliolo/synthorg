"""Unit tests for SQLiteSettingsRepository."""

import aiosqlite
import pytest

from synthorg.core.types import NotBlankStr
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.persistence.sqlite.settings_repo import SQLiteSettingsRepository
from tests._shared.persistence import make_private_write_context


def _row(namespace: str, key: str, value: str, ts: str) -> SettingRow:
    return SettingRow(
        namespace=NotBlankStr(namespace),
        key=NotBlankStr(key),
        value=value,
        updated_at=ts,
    )


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
        result = await repo.get((NotBlankStr("budget"), NotBlankStr("nonexistent")))
        assert result is None

    async def test_set_and_get(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "total_monthly", "200.0", "2026-03-16T10:00:00Z"))
        result = await repo.get((NotBlankStr("budget"), NotBlankStr("total_monthly")))
        assert result is not None
        assert result.value == "200.0"
        assert result.updated_at == "2026-03-16T10:00:00Z"

    async def test_set_upserts(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z"))
        await repo.save(_row("budget", "total_monthly", "300.0", "2026-03-16T11:00:00Z"))
        result = await repo.get((NotBlankStr("budget"), NotBlankStr("total_monthly")))
        assert result is not None
        assert result.value == "300.0"
        assert result.updated_at == "2026-03-16T11:00:00Z"

    async def test_delete_existing(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z"))
        deleted = await repo.delete(
            (NotBlankStr("budget"), NotBlankStr("total_monthly")),
        )
        assert deleted is True
        assert (
            await repo.get((NotBlankStr("budget"), NotBlankStr("total_monthly")))
            is None
        )

    async def test_delete_nonexistent(self, repo: SQLiteSettingsRepository) -> None:
        deleted = await repo.delete(
            (NotBlankStr("budget"), NotBlankStr("nonexistent")),
        )
        assert deleted is False

    async def test_get_namespace(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "b_key", "1", "2026-03-16T10:00:00Z"))
        await repo.save(_row("budget", "a_key", "2", "2026-03-16T10:00:00Z"))
        await repo.save(_row("security", "enabled", "true", "2026-03-16T10:00:00Z"))
        result = await repo.get_namespace(NotBlankStr("budget"))
        assert len(result) == 2
        # Sorted by key
        assert (result[0].key, result[0].value) == ("a_key", "2")
        assert (result[1].key, result[1].value) == ("b_key", "1")

    async def test_get_namespace_empty(self, repo: SQLiteSettingsRepository) -> None:
        result = await repo.get_namespace(NotBlankStr("nonexistent"))
        assert result == ()

    async def test_get_all(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "total_monthly", "100.0", "2026-03-16T10:00:00Z"))
        await repo.save(_row("security", "enabled", "true", "2026-03-16T10:00:00Z"))
        result = await repo.list_items(limit=100, offset=0)
        assert len(result) == 2
        # Sorted by (namespace, key)
        assert result[0].namespace == "budget"
        assert result[1].namespace == "security"

    async def test_get_all_empty(self, repo: SQLiteSettingsRepository) -> None:
        result = await repo.list_items(limit=100, offset=0)
        assert result == ()

    async def test_delete_namespace(self, repo: SQLiteSettingsRepository) -> None:
        await repo.save(_row("budget", "a", "1", "2026-03-16T10:00:00Z"))
        await repo.save(_row("budget", "b", "2", "2026-03-16T10:00:00Z"))
        await repo.save(_row("security", "c", "3", "2026-03-16T10:00:00Z"))
        count = await repo.delete_namespace(NotBlankStr("budget"))
        assert count == 2
        assert await repo.get_namespace(NotBlankStr("budget")) == ()
        # security remains
        assert len(await repo.get_namespace(NotBlankStr("security"))) == 1

    async def test_delete_namespace_empty(self, repo: SQLiteSettingsRepository) -> None:
        count = await repo.delete_namespace(NotBlankStr("nonexistent"))
        assert count == 0

    async def test_namespaces_are_isolated(
        self, repo: SQLiteSettingsRepository
    ) -> None:
        """Same key in different namespaces should be independent."""
        await repo.save(_row("budget", "enabled", "false", "2026-03-16T10:00:00Z"))
        await repo.save(_row("security", "enabled", "true", "2026-03-16T10:00:00Z"))
        budget_val = await repo.get((NotBlankStr("budget"), NotBlankStr("enabled")))
        security_val = await repo.get(
            (NotBlankStr("security"), NotBlankStr("enabled")),
        )
        assert budget_val is not None
        assert security_val is not None
        assert budget_val.value == "false"
        assert security_val.value == "true"

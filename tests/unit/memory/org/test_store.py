"""Tests for SQLiteOrgFactStore."""

import sqlite3
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.core.enums import OrgFactCategory, SeniorityLevel
from synthorg.memory.org.errors import (
    OrgMemoryConnectionError,
    OrgMemoryQueryError,
    OrgMemoryWriteError,
)
from synthorg.memory.org.models import OrgFact, OrgFactAuthor
from synthorg.memory.org.store import SQLiteOrgFactStore, _row_to_org_fact

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)
_HUMAN_AUTHOR = OrgFactAuthor(is_human=True)
_AGENT_AUTHOR = OrgFactAuthor(
    agent_id="agent-1",
    seniority=SeniorityLevel.SENIOR,
    is_human=False,
)


def _make_fact(
    fact_id: str = "fact-1",
    content: str = "Test fact",
    category: OrgFactCategory = OrgFactCategory.ADR,
    version: int = 1,
) -> OrgFact:
    return OrgFact(
        id=fact_id,
        content=content,
        category=category,
        author=_HUMAN_AUTHOR,
        created_at=_NOW,
        version=version,
    )


@pytest.mark.unit
class TestSQLiteOrgFactStoreLifecycle:
    """Connection lifecycle tests."""

    async def test_connect_disconnect(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        assert store.is_connected is True
        await store.disconnect()
        assert store.is_connected is False

    async def test_disconnect_when_not_connected(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.disconnect()

    async def test_double_connect_is_safe(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        await store.connect()
        assert store.is_connected is True
        await store.disconnect()

    async def test_backend_name(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        assert store.backend_name == "sqlite_org_facts"


@pytest.mark.unit
class TestSQLiteOrgFactStoreOperations:
    """CRUD operation tests."""

    async def test_save_and_get(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            fact = _make_fact()
            await store.save(fact)
            retrieved = await store.get("fact-1")
            assert retrieved is not None
            assert retrieved.id == "fact-1"
            assert retrieved.content == "Test fact"
            assert retrieved.category == OrgFactCategory.ADR
            assert retrieved.version == 1
        finally:
            await store.disconnect()

    async def test_get_nonexistent(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            result = await store.get("nonexistent")
            assert result is None
        finally:
            await store.disconnect()

    async def test_query_by_category(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1", "Fact A", OrgFactCategory.ADR))
            await store.save(_make_fact("f2", "Fact B", OrgFactCategory.PROCEDURE))
            await store.save(_make_fact("f3", "Fact C", OrgFactCategory.ADR))

            results = await store.query(
                categories=frozenset({OrgFactCategory.ADR}),
            )
            assert len(results) == 2
            assert all(r.category == OrgFactCategory.ADR for r in results)
        finally:
            await store.disconnect()

    async def test_query_by_text(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1", "Code review required"))
            await store.save(_make_fact("f2", "Deploy always on Friday"))

            results = await store.query(text="review")
            assert len(results) == 1
            assert results[0].id == "f1"
        finally:
            await store.disconnect()

    async def test_query_with_limit(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            for i in range(10):
                await store.save(_make_fact(f"f{i}", f"Fact {i}"))
            results = await store.query(limit=3)
            assert len(results) == 3
        finally:
            await store.disconnect()

    async def test_list_by_category(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1", category=OrgFactCategory.CONVENTION))
            await store.save(_make_fact("f2", category=OrgFactCategory.CONVENTION))
            await store.save(_make_fact("f3", category=OrgFactCategory.ADR))

            results = await store.list_by_category(OrgFactCategory.CONVENTION)
            assert len(results) == 2
        finally:
            await store.disconnect()

    async def test_delete(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1"))
            assert await store.delete("f1") is True
            assert await store.get("f1") is None
        finally:
            await store.disconnect()

    async def test_delete_nonexistent(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            assert await store.delete("nonexistent") is False
        finally:
            await store.disconnect()

    async def test_save_with_agent_author(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            fact = OrgFact(
                id="f1",
                content="Agent fact",
                category=OrgFactCategory.ADR,
                author=_AGENT_AUTHOR,
                created_at=_NOW,
                version=1,
            )
            await store.save(fact)
            retrieved = await store.get("f1")
            assert retrieved is not None
            assert retrieved.author.agent_id == "agent-1"
            assert retrieved.author.seniority == SeniorityLevel.SENIOR
            assert retrieved.author.is_human is False
        finally:
            await store.disconnect()

    async def test_operations_when_not_connected_raise(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        with pytest.raises(OrgMemoryConnectionError):
            await store.save(_make_fact())
        with pytest.raises(OrgMemoryConnectionError):
            await store.get("f1")
        with pytest.raises(OrgMemoryConnectionError):
            await store.query()
        with pytest.raises(OrgMemoryConnectionError):
            await store.delete("f1")

    async def test_list_by_category_when_not_connected(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        with pytest.raises(OrgMemoryConnectionError):
            await store.list_by_category(OrgFactCategory.ADR)

    async def test_save_duplicate_id_raises(self) -> None:
        """INSERT (not INSERT OR REPLACE) preserves audit trail."""
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1", "Original content"))
            with pytest.raises(OrgMemoryWriteError):
                await store.save(_make_fact("f1", "Updated content"))
            # Original still intact
            retrieved = await store.get("f1")
            assert retrieved is not None
            assert retrieved.content == "Original content"
        finally:
            await store.disconnect()

    async def test_query_combined_category_and_text(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(
                _make_fact("f1", "Code review required", OrgFactCategory.ADR),
            )
            await store.save(
                _make_fact("f2", "Code review optional", OrgFactCategory.PROCEDURE),
            )
            await store.save(
                _make_fact("f3", "Deploy on Friday", OrgFactCategory.ADR),
            )

            results = await store.query(
                categories=frozenset({OrgFactCategory.ADR}),
                text="review",
            )
            assert len(results) == 1
            assert results[0].id == "f1"
        finally:
            await store.disconnect()

    async def test_connect_with_invalid_path(self) -> None:
        store = SQLiteOrgFactStore("/nonexistent/dir/db.sqlite")
        with pytest.raises(OrgMemoryConnectionError) as exc_info:
            await store.connect()
        assert exc_info.value.__cause__ is not None

    async def test_save_sqlite_error_wraps(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            with (
                patch.object(
                    store._db,
                    "execute",
                    side_effect=sqlite3.Error("disk I/O error"),
                ),
                pytest.raises(OrgMemoryWriteError, match="disk I/O error"),
            ):
                await store.save(_make_fact())
        finally:
            await store.disconnect()

    async def test_get_sqlite_error_wraps(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            with (
                patch.object(
                    store._db,
                    "execute",
                    side_effect=sqlite3.Error("disk I/O error"),
                ),
                pytest.raises(OrgMemoryQueryError, match="disk I/O error"),
            ):
                await store.get("f1")
        finally:
            await store.disconnect()

    async def test_query_sqlite_error_wraps(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            with (
                patch.object(
                    store._db,
                    "execute",
                    side_effect=sqlite3.Error("disk I/O error"),
                ),
                pytest.raises(OrgMemoryQueryError, match="disk I/O error"),
            ):
                await store.query()
        finally:
            await store.disconnect()

    async def test_delete_sqlite_error_wraps(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            with (
                patch.object(
                    store._db,
                    "execute",
                    side_effect=sqlite3.Error("disk I/O error"),
                ),
                pytest.raises(OrgMemoryWriteError, match="disk I/O error"),
            ):
                await store.delete("f1")
        finally:
            await store.disconnect()

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(OrgMemoryConnectionError, match="Path traversal"):
            SQLiteOrgFactStore("../../../etc/db")

    async def test_like_special_chars_escaped(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            await store.save(_make_fact("f1", "100% complete"))
            await store.save(_make_fact("f2", "field_name here"))
            await store.save(_make_fact("f3", "normal text"))

            results_percent = await store.query(text="%")
            assert len(results_percent) == 1
            assert results_percent[0].id == "f1"

            results_underscore = await store.query(text="_")
            assert len(results_underscore) == 1
            assert results_underscore[0].id == "f2"
        finally:
            await store.disconnect()

    async def test_list_by_category_sqlite_error_wraps(self) -> None:
        """Item 12: list_by_category wraps sqlite3.Error."""
        store = SQLiteOrgFactStore(":memory:")
        await store.connect()
        try:
            with (
                patch.object(
                    store._db,
                    "execute",
                    side_effect=sqlite3.Error("disk I/O error"),
                ),
                pytest.raises(OrgMemoryQueryError, match="disk I/O error"),
            ):
                await store.list_by_category(OrgFactCategory.ADR)
        finally:
            await store.disconnect()

    async def test_row_parse_error_wraps_in_query_error(self) -> None:
        malformed_row = {
            "id": "f1",
            "content": "test",
            "category": "INVALID_CATEGORY",
            "author_agent_id": None,
            "author_seniority": None,
            "author_is_human": 1,
            "created_at": _NOW.isoformat(),
            "version": 1,
        }
        mock_row = AsyncMock()
        mock_row.__getitem__ = lambda self, key: malformed_row[key]
        with pytest.raises(OrgMemoryQueryError, match="Failed to deserialize"):
            _row_to_org_fact(mock_row)

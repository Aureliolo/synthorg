"""Unit tests for SqlQueryTool."""

import aiosqlite
import pytest
from pydantic import ValidationError

from synthorg.tools.database.config import DatabaseConnectionConfig
from synthorg.tools.database.sql_query import SqlQueryTool, _classify_statement

# ── Statement classification ───────────────────────────────────


class TestClassifyStatement:
    """Tests for SQL statement classification."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("SELECT * FROM users", "SELECT"),
            ("  select name from t", "SELECT"),
            ("INSERT INTO t VALUES (1)", "INSERT"),
            ("UPDATE t SET x=1", "UPDATE"),
            ("DELETE FROM t WHERE id=1", "DELETE"),
            ("DROP TABLE t", "DROP"),
            ("CREATE TABLE t (id INT)", "CREATE"),
            ("EXPLAIN SELECT 1", "EXPLAIN"),
            ("PRAGMA table_info(t)", "PRAGMA"),
            ("-- comment\nSELECT 1", "SELECT"),
            ("/* block */ SELECT 1", "SELECT"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_classify(self, query: str, expected: str) -> None:
        assert _classify_statement(query) == expected


# ── Read-only enforcement ──────────────────────────────────────


class TestReadOnlyEnforcement:
    """Tests for write query blocking in read-only mode."""

    @pytest.mark.unit
    async def test_select_allowed(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SqlQueryTool(config=read_only_config)
        # Create the database first
        async with aiosqlite.connect(read_only_config.database_path) as db:
            await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
            await db.execute("INSERT INTO t VALUES (1, 'test')")
            await db.commit()

        result = await tool.execute(arguments={"query": "SELECT * FROM t"})
        assert result.is_error is False
        assert "test" in result.content

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "query",
        [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET name='x'",
            "DELETE FROM t WHERE id=1",
            "DROP TABLE t",
            "CREATE TABLE t2 (id INT)",
        ],
    )
    async def test_write_blocked_in_read_only(
        self,
        read_only_config: DatabaseConnectionConfig,
        query: str,
    ) -> None:
        tool = SqlQueryTool(config=read_only_config)
        result = await tool.execute(arguments={"query": query})
        assert result.is_error is True
        assert "blocked" in result.content.lower()


# ── Write mode ─────────────────────────────────────────────────


class TestWriteMode:
    """Tests for write queries with writable config."""

    @pytest.mark.unit
    async def test_insert_allowed_when_writable(
        self, writable_config: DatabaseConnectionConfig
    ) -> None:
        tool = SqlQueryTool(config=writable_config)
        async with aiosqlite.connect(writable_config.database_path) as db:
            await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
            await db.commit()

        result = await tool.execute(
            arguments={"query": "INSERT INTO t VALUES (1, 'test')"}
        )
        assert result.is_error is False
        assert "affected" in result.content.lower()


# ── Query execution ────────────────────────────────────────────


class TestQueryExecution:
    """Tests for actual query execution."""

    @pytest.mark.unit
    async def test_empty_query(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SqlQueryTool(config=read_only_config)
        # ``query`` is ``NotBlankStr`` on ``SqlQueryArgs``; a blank query
        # is rejected at the ``parse_typed`` boundary before execution.
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"query": ""})

    @pytest.mark.unit
    async def test_no_results(self, read_only_config: DatabaseConnectionConfig) -> None:
        tool = SqlQueryTool(config=read_only_config)
        async with aiosqlite.connect(read_only_config.database_path) as db:
            await db.execute("CREATE TABLE t (id INTEGER)")
            await db.commit()

        result = await tool.execute(arguments={"query": "SELECT * FROM t"})
        assert result.is_error is False
        assert "no results" in result.content.lower()

    @pytest.mark.unit
    async def test_parameterized_query(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SqlQueryTool(config=read_only_config)
        async with aiosqlite.connect(read_only_config.database_path) as db:
            await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
            await db.execute("INSERT INTO t VALUES (1, 'alice')")
            await db.execute("INSERT INTO t VALUES (2, 'bob')")
            await db.commit()

        result = await tool.execute(
            arguments={
                "query": "SELECT * FROM t WHERE name = ?",
                "parameters": ["alice"],
            }
        )
        assert result.is_error is False
        assert "alice" in result.content
        assert "bob" not in result.content

    @pytest.mark.unit
    async def test_invalid_sql_returns_error(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SqlQueryTool(config=read_only_config)
        result = await tool.execute(arguments={"query": "SELECT FROM"})
        assert result.is_error is True
        assert "failed" in result.content.lower() or "error" in result.content.lower()

"""Unit tests for SchemaInspectTool."""

import aiosqlite
import pytest
from pydantic import ValidationError

from synthorg.tools.database.config import DatabaseConnectionConfig
from synthorg.tools.database.schema_inspect import SchemaInspectTool


class TestListTables:
    """Tests for table listing."""

    @pytest.mark.unit
    async def test_list_tables(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        async with aiosqlite.connect(read_only_config.database_path) as db:
            await db.execute("CREATE TABLE users (id INTEGER, name TEXT)")
            await db.execute("CREATE TABLE orders (id INTEGER, user_id INTEGER)")
            await db.commit()

        result = await tool.execute(arguments={"action": "list_tables"})
        assert result.is_error is False
        assert "users" in result.content
        assert "orders" in result.content

    @pytest.mark.unit
    async def test_empty_database(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        # Ensure DB file exists by connecting
        async with aiosqlite.connect(read_only_config.database_path):
            pass

        result = await tool.execute(arguments={"action": "list_tables"})
        assert result.is_error is False
        assert "no tables" in result.content.lower()


class TestDescribeTable:
    """Tests for table column description."""

    @pytest.mark.unit
    async def test_describe_table(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        async with aiosqlite.connect(read_only_config.database_path) as db:
            await db.execute(
                "CREATE TABLE users ("
                "  id INTEGER PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  email TEXT"
                ")"
            )
            await db.commit()

        result = await tool.execute(
            arguments={"action": "describe_table", "table_name": "users"}
        )
        assert result.is_error is False
        assert "id" in result.content
        assert "name" in result.content
        assert "email" in result.content

    @pytest.mark.unit
    async def test_nonexistent_table(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        async with aiosqlite.connect(read_only_config.database_path):
            pass

        result = await tool.execute(
            arguments={"action": "describe_table", "table_name": "nonexistent"}
        )
        assert result.is_error is True

    @pytest.mark.unit
    async def test_missing_table_name(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        # ``SchemaInspectArgs`` requires table_name for describe_table at
        # the ``parse_typed`` boundary.
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "describe_table"})

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "table_name",
        [
            "users; DROP TABLE secrets",
            "users WHERE 1=1",
            "users-table",
            "'; DELETE FROM users; --",
        ],
    )
    async def test_unsafe_table_name_rejected(
        self,
        read_only_config: DatabaseConnectionConfig,
        table_name: str,
    ) -> None:
        # ``table_name`` is only presence-validated at the typed boundary,
        # so the ``_SAFE_IDENTIFIER_RE`` guard in ``_describe_table`` is the
        # sole SQL-injection barrier for this path.
        tool = SchemaInspectTool(config=read_only_config)
        result = await tool.execute(
            arguments={"action": "describe_table", "table_name": table_name}
        )
        assert result.is_error is True
        assert "invalid table name" in result.content.lower()


class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.unit
    async def test_invalid_action(
        self, read_only_config: DatabaseConnectionConfig
    ) -> None:
        tool = SchemaInspectTool(config=read_only_config)
        # ``action`` is a closed Literal on ``SchemaInspectArgs``.
        with pytest.raises(ValidationError):
            await tool.execute(arguments={"action": "drop_all"})

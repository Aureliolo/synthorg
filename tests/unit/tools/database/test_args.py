"""Tests for typed database tool argument models."""

import pytest
from pydantic import ValidationError

from synthorg.tools.database._args import (
    SchemaInspectArgs,
    SqlQueryArgs,
)


class TestSqlQueryArgs:
    @pytest.mark.unit
    def test_minimal(self) -> None:
        args = SqlQueryArgs(query="SELECT * FROM users")
        assert args.parameters == ()

    @pytest.mark.unit
    def test_scalar_parameters(self) -> None:
        args = SqlQueryArgs(
            query="SELECT * FROM x WHERE id = ?",
            parameters=(1, "alice", 3.14, True, None),
        )
        assert args.parameters == (1, "alice", 3.14, True, None)

    @pytest.mark.unit
    def test_bytes_parameter_supported(self) -> None:
        """SQLite's bind protocol accepts bytes for BLOB columns."""
        args = SqlQueryArgs(query="x", parameters=(b"binary",))
        assert args.parameters == (b"binary",)

    @pytest.mark.unit
    def test_non_scalar_parameter_rejected(self) -> None:
        """Lists and dicts are not valid SQL bind values."""
        with pytest.raises(ValidationError):
            SqlQueryArgs.model_validate(
                {"query": "x", "parameters": [[1, 2, 3]]},
            )

    @pytest.mark.unit
    def test_blank_query_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SqlQueryArgs(query="   ")


class TestSchemaInspectArgs:
    @pytest.mark.unit
    def test_list_tables(self) -> None:
        args = SchemaInspectArgs(action="list_tables")
        assert args.action == "list_tables"
        assert args.table_name is None

    @pytest.mark.unit
    def test_describe_table(self) -> None:
        args = SchemaInspectArgs(action="describe_table", table_name="users")
        assert args.table_name == "users"

    @pytest.mark.unit
    def test_action_is_closed_literal(self) -> None:
        with pytest.raises(ValidationError):
            SchemaInspectArgs.model_validate({"action": "drop_table"})

    @pytest.mark.unit
    def test_blank_table_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SchemaInspectArgs(action="describe_table", table_name="   ")

    @pytest.mark.unit
    def test_describe_table_requires_table_name(self) -> None:
        """``action='describe_table'`` without ``table_name`` is rejected."""
        with pytest.raises(ValidationError):
            SchemaInspectArgs.model_validate({"action": "describe_table"})

    @pytest.mark.unit
    def test_list_tables_rejects_table_name(self) -> None:
        """``action='list_tables'`` must not include ``table_name``."""
        with pytest.raises(ValidationError):
            SchemaInspectArgs.model_validate(
                {"action": "list_tables", "table_name": "users"},
            )

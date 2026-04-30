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
        """Database bind protocols accept bytes for binary columns."""
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
    @pytest.mark.parametrize(
        ("payload", "case"),
        [
            ({"action": "drop_table"}, "unknown action literal"),
            ({"action": "describe_table"}, "describe_table without table_name"),
            (
                {"action": "describe_table", "table_name": "   "},
                "describe_table with blank table_name",
            ),
            (
                {"action": "list_tables", "table_name": "users"},
                "list_tables with table_name",
            ),
        ],
        ids=[
            "unknown_action",
            "missing_table_name",
            "blank_table_name",
            "extra_table_name",
        ],
    )
    def test_invalid_shape_rejected(
        self,
        payload: dict[str, str],
        case: str,
    ) -> None:
        """Every malformed SchemaInspectArgs shape fails validation."""
        with pytest.raises(ValidationError):
            SchemaInspectArgs.model_validate(payload)
        # ``case`` is parametrize id sugar; assigning it to ``_`` keeps
        # mypy happy without triggering ARG002.
        assert isinstance(case, str)

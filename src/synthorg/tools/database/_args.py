"""Typed argument models for database tools.

Tools wired to consume these models:

* :class:`~synthorg.tools.database.sql_query.SqlQueryTool` -> :class:`SqlQueryArgs`
* :class:`~synthorg.tools.database.schema_inspect.SchemaInspectTool`
  -> :class:`SchemaInspectArgs`
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


# JSON-value alias for SQL bind parameters.  The wire schema declares
# ``items: {}`` (anything); SQLite's parameter binding is documented to
# accept str / int / float / bool / None / bytes for ``?`` placeholders.
# Nested arrays/dicts have no meaningful binding semantics so the
# typed surface restricts to scalars; tools that need richer payloads
# round-trip through JSON columns at the application layer instead.
type SqlBindValue = str | int | float | bool | bytes | None


SchemaInspectAction = Literal["list_tables", "describe_table"]


class SqlQueryArgs(BaseModel):
    """Args for ``sql_query``.

    Read-only / write enforcement, statement classification, and the
    "no PRAGMA" carve-out stay inside the tool body because they
    depend on per-tool ``read_only`` configuration.
    """

    model_config = _ARGS_CONFIG

    query: NotBlankStr = Field(description="SQL query to execute")
    parameters: tuple[SqlBindValue, ...] = Field(
        default=(),
        description="Query parameters bound to ``?`` placeholders",
    )


class SchemaInspectArgs(BaseModel):
    """Args for ``schema_inspect``.

    The ``table_name is required for describe_table`` cross-field
    constraint stays in the tool body so the LLM-facing message
    references the action that triggered it.
    """

    model_config = _ARGS_CONFIG

    action: SchemaInspectAction = Field(description="Inspection action")
    table_name: NotBlankStr | None = Field(
        default=None,
        description="Table name (required for describe_table)",
    )


__all__ = [
    "SchemaInspectAction",
    "SchemaInspectArgs",
    "SqlBindValue",
    "SqlQueryArgs",
]

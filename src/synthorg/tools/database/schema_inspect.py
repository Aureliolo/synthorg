"""Schema inspection tool -- inspect database structure.

Provides table listing and column description for SQLite databases.
Always read-only.
"""

import asyncio
import re
from typing import ClassVar, Final, cast, override

from pydantic import BaseModel, JsonValue

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.database import (
    DB_SCHEMA_INSPECT_FAILED,
    DB_SCHEMA_INSPECT_START,
    DB_SCHEMA_INSPECT_SUCCESS,
)
from synthorg.persistence.external_sql import (
    describe_external_table,
    list_external_tables,
)
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.database._args import SchemaInspectArgs
from synthorg.tools.database.base_db_tool import BaseDatabaseTool
from synthorg.tools.database.config import DatabaseConnectionConfig

logger = get_logger(__name__)

_SAFE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_ACTIONS: Final[tuple[str, ...]] = ("list_tables", "describe_table")


class SchemaInspectTool(BaseDatabaseTool):
    """Inspect database schema: list tables or describe columns.

    Always read-only.  Uses SQLite ``sqlite_master`` and
    ``PRAGMA table_info`` for metadata queries.

    Examples:
        List all tables::

            tool = SchemaInspectTool(config=db_config)
            result = await tool.execute(arguments={"action": "list_tables"})
    """

    args_model: ClassVar[type[BaseModel] | None] = SchemaInspectArgs

    def __init__(self, *, config: DatabaseConnectionConfig) -> None:
        """Initialize the schema inspection tool.

        Args:
            config: Database connection settings (driver, DSN, query
                timeouts, allowlists).
        """
        super().__init__(
            name="schema_inspect",
            description=(
                "Inspect database schema: list tables or describe table columns."
            ),
            parameters_schema=SchemaInspectArgs.model_json_schema(),
            action_type=ActionType.DB_QUERY,
            config=config,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Inspect the database schema.

        Args:
            arguments: Must contain ``action``; ``table_name`` required
                for ``describe_table``.

        Returns:
            A ``ToolExecutionResult`` with schema information.
        """
        action = cast("str", arguments["action"])
        table_name = cast("str | None", arguments.get("table_name"))

        if action not in _ACTIONS:
            return ToolExecutionResult(
                content=(f"Invalid action: {action!r}. Must be one of: {_ACTIONS}"),
                is_error=True,
            )

        if action == "describe_table" and not table_name:
            return ToolExecutionResult(
                content="table_name is required for describe_table",
                is_error=True,
            )

        logger.info(
            DB_SCHEMA_INSPECT_START,
            action=action,
            table_name=table_name,
            database=self._config.database_path,
        )

        try:
            coro = (
                self._list_tables()
                if action == "list_tables"
                else self._describe_table(table_name or "")
            )
            return await asyncio.wait_for(
                coro,
                timeout=self._config.query_timeout,
            )
        except TimeoutError:
            logger.warning(
                DB_SCHEMA_INSPECT_FAILED,
                action=action,
                error="timed out",
                timeout=self._config.query_timeout,
            )
            return ToolExecutionResult(
                content=(
                    f"Schema inspection timed out after {self._config.query_timeout}s"
                ),
                is_error=True,
            )
        except QueryError as exc:
            logger.warning(
                DB_SCHEMA_INSPECT_FAILED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Schema inspection failed: {safe_error_description(exc)}",
                is_error=True,
            )

    async def _list_tables(self) -> ToolExecutionResult:
        """List all tables in the database.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        tables = list(
            await list_external_tables(database_path=self._config.database_path)
        )

        if not tables:
            logger.info(DB_SCHEMA_INSPECT_SUCCESS, action="list_tables", count=0)
            return ToolExecutionResult(
                content="No tables found.",
                metadata={"action": "list_tables", "count": 0},
            )

        content = "Tables:\n" + "\n".join(f"  - {t}" for t in tables)
        logger.info(
            DB_SCHEMA_INSPECT_SUCCESS,
            action="list_tables",
            count=len(tables),
        )
        tables_meta = cast("list[JsonValue]", tables)
        return ToolExecutionResult(
            content=content,
            metadata={"action": "list_tables", "tables": tables_meta},
        )

    async def _describe_table(self, table_name: str) -> ToolExecutionResult:
        """Describe columns of a specific table.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        if not _SAFE_IDENTIFIER_RE.match(table_name):
            logger.warning(
                DB_SCHEMA_INSPECT_FAILED,
                action="describe_table",
                error=f"Invalid table name: {table_name!r}",
            )
            return ToolExecutionResult(
                content=f"Invalid table name: {table_name!r}. "
                "Must be alphanumeric/underscore.",
                is_error=True,
            )
        table_columns = await describe_external_table(
            database_path=self._config.database_path,
            table_name=table_name,
        )

        if not table_columns:
            return ToolExecutionResult(
                content=f"Table {table_name!r} not found or has no columns.",
                is_error=True,
            )

        lines = [f"Table: {table_name}", ""]
        lines.append("name | type | notnull | default | pk")
        lines.append("-" * 50)
        columns = []
        for col in table_columns:
            lines.append(
                f"{col.name} | {col.type} | {col.notnull} | "
                f"{col.default} | {col.primary_key}"
            )
            columns.append(col.name)

        logger.info(
            DB_SCHEMA_INSPECT_SUCCESS,
            action="describe_table",
            table=table_name,
            column_count=len(columns),
        )
        columns_meta = cast("list[JsonValue]", columns)
        return ToolExecutionResult(
            content="\n".join(lines),
            metadata={
                "action": "describe_table",
                "table": table_name,
                "columns": columns_meta,
            },
        )

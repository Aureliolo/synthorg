"""SQL query tool -- execute SQL queries against a configured database.

Read-only by default.  Write queries (INSERT, UPDATE, DELETE, etc.)
are rejected unless the connection config has ``read_only=False``.
Uses parameterized queries to prevent SQL injection.

Defense-in-depth: read-only mode uses an allowlist (SELECT, EXPLAIN)
rather than a denylist.  WITH and PRAGMA are intentionally blocked in
read-only mode because WITH can prefix DML (WITH ... INSERT) and
PRAGMA can perform writes (PRAGMA writable_schema=ON).  The SQLite
URI ``mode=ro`` provides a second enforcement layer at the database
level.  ATTACH, DETACH, and VACUUM are unconditionally blocked to
prevent filesystem escape regardless of read_only setting.
"""

import asyncio
import re
import urllib.parse
from typing import ClassVar, Final, cast, override

import aiosqlite
from pydantic import BaseModel, JsonValue

from synthorg.core.enums import ActionType
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.database import (
    DB_QUERY_FAILED,
    DB_QUERY_START,
    DB_QUERY_SUCCESS,
    DB_QUERY_TIMEOUT,
    DB_WRITE_BLOCKED,
)
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.database._args import SqlBindValue, SqlQueryArgs
from synthorg.tools.database.base_db_tool import BaseDatabaseTool
from synthorg.tools.database.config import DatabaseConnectionConfig

logger = get_logger(__name__)

# Statement prefixes that are always considered read-only.
# NOTE: WITH and PRAGMA are intentionally excluded -- WITH can prefix
# DML (e.g. WITH ... INSERT), and PRAGMA can perform writes
# (e.g. PRAGMA writable_schema=ON).  Both require write access.
_READ_ONLY_PREFIXES: Final[tuple[str, ...]] = (
    "SELECT",
    "EXPLAIN",
)

# Statements that can affect the filesystem beyond the configured DB.
# Always blocked regardless of read_only setting.
_ALWAYS_BLOCKED_PREFIXES: Final[tuple[str, ...]] = (
    "ATTACH",
    "DETACH",
    "VACUUM",
)

# Statement prefixes that require write access.
_WRITE_PREFIXES: Final[tuple[str, ...]] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "REINDEX",
)

_LEADING_COMMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:--[^\n]*(?:\n|$)|/\*.*?\*/)\s*)*",
    re.DOTALL,
)


def _classify_statement(query: str) -> str:
    """Return the uppercase first keyword of a SQL statement.

    Strips leading whitespace and SQL comments (``--`` and ``/* */``).

    Args:
        query: Raw SQL query string.

    Returns:
        The first keyword in uppercase, or empty string if empty.
    """
    stripped = _LEADING_COMMENT_RE.sub("", query).strip()
    if not stripped:
        return ""
    first_word = stripped.split(maxsplit=1)[0]
    return first_word.upper()


class SqlQueryTool(BaseDatabaseTool):
    """Execute SQL queries against a configured SQLite database.

    Read-only by default: rejects INSERT, UPDATE, DELETE, DROP, etc.
    unless the connection config has ``read_only=False``.  Write
    queries use ``ActionType.DB_MUTATE`` for security escalation.

    Uses parameterized queries to prevent SQL injection.

    Examples:
        Execute a read-only query::

            tool = SqlQueryTool(config=db_config)
            result = await tool.execute(
                arguments={"query": "SELECT * FROM users LIMIT 10"}
            )
    """

    args_model: ClassVar[type[BaseModel] | None] = SqlQueryArgs

    def __init__(self, *, config: DatabaseConnectionConfig) -> None:
        """Initialize the SQL query tool.

        Args:
            config: Database connection settings. The action type
                resolves from ``config.read_only`` so security
                policies can gate write-capable connections.
        """
        # Use DB_MUTATE when writes are permitted so security
        # policies can gate write-capable connections appropriately.
        action = ActionType.DB_QUERY if config.read_only else ActionType.DB_MUTATE
        super().__init__(
            name="sql_query",
            description=(
                "Execute SQL queries against a database. "
                "Read-only by default; write queries require "
                "explicit configuration."
            ),
            parameters_schema=SqlQueryArgs.model_json_schema(),
            action_type=action,
            config=config,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a SQL query.

        Args:
            arguments: Must contain ``query``; optionally ``parameters``.

        Returns:
            A ``ToolExecutionResult`` with formatted query results.
        """
        query = cast("str", arguments["query"])
        raw_parameters = arguments.get("parameters")
        parameters: list[SqlBindValue] = (
            [cast("SqlBindValue", param) for param in raw_parameters]
            if isinstance(raw_parameters, (list, tuple))
            else []
        )

        keyword = _classify_statement(query)
        if not keyword:
            return ToolExecutionResult(
                content="Empty query",
                is_error=True,
            )

        # Block filesystem-affecting statements unconditionally.
        if keyword in _ALWAYS_BLOCKED_PREFIXES:
            logger.warning(
                DB_WRITE_BLOCKED,
                keyword=keyword,
                database=self._config.database_path,
            )
            return ToolExecutionResult(
                content=(
                    f"{keyword} statements are blocked for security "
                    f"(filesystem escape prevention)"
                ),
                is_error=True,
            )

        # Read-only enforcement: only SELECT/EXPLAIN are allowed in
        # read-only mode.  Everything else (including WITH, PRAGMA, and
        # unrecognised keywords) requires write access.
        is_read = keyword in _READ_ONLY_PREFIXES
        is_write = not is_read
        if is_write and self._config.read_only:
            logger.warning(
                DB_WRITE_BLOCKED,
                keyword=keyword,
                database=self._config.database_path,
            )
            return ToolExecutionResult(
                content=(
                    f"Write query blocked: {keyword} statements are not "
                    f"allowed in read-only mode"
                ),
                is_error=True,
            )

        logger.info(
            DB_QUERY_START,
            keyword=keyword,
            is_write=is_write,
            database=self._config.database_path,
        )

        return await self._execute_query(query, parameters, keyword, is_write)

    async def _execute_query(
        self,
        query: str,
        parameters: list[SqlBindValue],
        keyword: str,
        is_write: bool,  # noqa: FBT001  -- private method
    ) -> ToolExecutionResult:
        """Execute the query against SQLite.

        Args:
            query: SQL query string.
            parameters: Query parameters.
            keyword: First keyword of the statement.
            is_write: Whether this is a write operation.

        Returns:
            A ``ToolExecutionResult`` with the result.
        """
        try:
            return await asyncio.wait_for(
                self._run_query(query, parameters, keyword, is_write),
                timeout=self._config.query_timeout,
            )
        except TimeoutError:
            logger.warning(
                DB_QUERY_TIMEOUT,
                database=self._config.database_path,
                timeout=self._config.query_timeout,
            )
            return ToolExecutionResult(
                content=(f"Query timed out after {self._config.query_timeout}s"),
                is_error=True,
            )
        except aiosqlite.Error as exc:
            logger.warning(
                DB_QUERY_FAILED,
                database=self._config.database_path,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content="Query execution failed.",
                is_error=True,
            )

    async def _run_query(
        self,
        query: str,
        parameters: list[SqlBindValue],
        keyword: str,
        is_write: bool,  # noqa: FBT001  -- private method
    ) -> ToolExecutionResult:
        """Execute the query against SQLite (inner coroutine for timeout wrapping).

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        if self._config.read_only:
            encoded = urllib.parse.quote(str(self._config.database_path))
            db_uri = f"file:{encoded}?mode=ro"
            db_conn = aiosqlite.connect(db_uri, uri=True)
        else:
            db_conn = aiosqlite.connect(self._config.database_path)
        async with db_conn as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, parameters)

            # For write DML that doesn't return rows, commit and
            # report rowcount.  For row-returning statements (SELECT,
            # INSERT RETURNING, PRAGMA, WITH SELECT), fetch below.
            if is_write and not cursor.description:
                await db.commit()
                content = f"{keyword} affected {cursor.rowcount} row(s)"
                logger.info(
                    DB_QUERY_SUCCESS,
                    keyword=keyword,
                    rowcount=cursor.rowcount,
                )
                return ToolExecutionResult(
                    content=content,
                    metadata={
                        "keyword": keyword,
                        "rowcount": cursor.rowcount,
                    },
                )

            # Row-returning statement -- fetch bounded rows, then
            # close the cursor before committing.
            desc = cursor.description
            limit = self._config.max_rows
            rows = list(await cursor.fetchmany(limit + 1))
            await cursor.close()
            if is_write:
                await db.commit()
            row_truncated = len(rows) > limit
            if row_truncated:
                rows = rows[:limit]

            if not rows:
                logger.info(DB_QUERY_SUCCESS, keyword=keyword, row_count=0)
                return ToolExecutionResult(
                    content="Query returned no results.",
                    metadata={"keyword": keyword, "row_count": 0},
                )

            columns = [d[0] for d in (desc or [])]
            columns_meta = cast("list[JsonValue]", columns)
            content = self._format_results(columns, rows)
            if row_truncated:
                content += f"\n\n[Truncated: result exceeded {limit:,} rows]"
            logger.info(
                DB_QUERY_SUCCESS,
                keyword=keyword,
                row_count=len(rows),
                column_count=len(columns),
                truncated=row_truncated,
            )
            return ToolExecutionResult(
                content=content,
                metadata={
                    "keyword": keyword,
                    "row_count": len(rows),
                    "columns": columns_meta,
                    "truncated": row_truncated,
                },
            )

    @staticmethod
    def _format_results(
        columns: list[str],
        rows: list[aiosqlite.Row],
    ) -> str:
        """Format query results as a table.

        Args:
            columns: Column names.
            rows: Result rows (each row is indexable by column position).

        Returns:
            Formatted table string.
        """
        lines: list[str] = []
        header = " | ".join(columns)
        lines.append(header)
        lines.append("-" * len(header))
        for row in rows:
            values = [str(row[i]) for i in range(len(columns))]
            lines.append(" | ".join(values))
        return "\n".join(lines)

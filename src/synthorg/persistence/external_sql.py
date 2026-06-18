# module-kind: adapter
"""External-SQLite access for agent-facing database tools.

The agent ``sql_query`` / ``schema_inspect`` tools let an agent run SQL
against an *operator-configured external SQLite database* (not synthorg's
own persistence backend). The DB driver, the raw SQL, the read-only URI
handling, and the transaction discipline all live here so the
persistence boundary holds with no allowlist exception: the tools own
policy (statement classification, read-only enforcement, security
action-type gating) and presentation (table formatting), and import no
driver.

The helpers take primitive parameters (path, flags) rather than a tools
config object so persistence never imports upward into ``tools``. Driver
failures are wrapped in :class:`QueryError` so callers branch on a domain
error rather than a driver type.
"""

import contextlib
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from synthorg.core.persistence_errors import QueryError


@dataclass(frozen=True, slots=True)
class ExternalDatabase:
    """Connection coordinates for an operator-configured external database.

    Attributes:
        database_path: Filesystem path to the SQLite database.
        read_only: Open the connection through a ``mode=ro`` URI.
    """

    database_path: str | Path
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class ExternalQueryResult:
    """Outcome of an external SQL statement execution.

    Attributes:
        returned_rows: ``True`` when the statement produced a result set
            (SELECT, EXPLAIN, or a write with RETURNING); ``False`` for a
            plain write whose only signal is ``rowcount``.
        columns: Result-set column names (empty for a non-row write).
        rows: Result rows as plain tuples (driver row type does not leak).
        rowcount: ``cursor.rowcount`` after execution.
        truncated: ``True`` when more than ``max_rows`` rows were
            available and the surplus was dropped.
    """

    returned_rows: bool
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    rowcount: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ExternalColumn:
    """A single column from ``PRAGMA table_info``.

    Attributes:
        name: Column name.
        type: Declared column type (may be empty in SQLite).
        notnull: Whether a ``NOT NULL`` constraint applies.
        default: Default value expression, or ``None``.
        primary_key: Whether the column participates in the primary key.
    """

    name: str
    type: str
    notnull: bool
    default: object
    primary_key: bool


def _connect(database_path: str | Path, *, read_only: bool) -> aiosqlite.Connection:
    """Open an external SQLite connection, read-only via URI when asked.

    Args:
        database_path: Filesystem path to the SQLite database.
        read_only: When ``True``, open through a ``file:...?mode=ro`` URI
            so the database driver rejects writes as a second enforcement
            layer beneath the tool's statement allowlist.

    Returns:
        An unopened ``aiosqlite.Connection`` awaitable context manager.
    """
    if read_only:
        encoded = urllib.parse.quote(str(database_path))
        return aiosqlite.connect(f"file:{encoded}?mode=ro", uri=True)
    return aiosqlite.connect(database_path)


def _quote_identifier(identifier: str) -> str:
    """Return a SQLite-double-quoted identifier with embedded quotes escaped.

    Args:
        identifier: A table or column name (already validated by the
            caller's identifier allowlist; quoting is defence-in-depth).

    Returns:
        The identifier wrapped in double quotes, with any ``"`` doubled.
    """
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


async def execute_external_query(
    database: ExternalDatabase,
    *,
    query: str,
    parameters: Sequence[object],
    is_write: bool,
    max_rows: int,
) -> ExternalQueryResult:
    """Execute one SQL statement against an external SQLite database.

    Write statements run inside an explicit transaction so a failure
    while fetching a RETURNING result set rolls back deterministically
    rather than silently discarding the write when the connection
    closes. Reads run against a read-only connection.

    Args:
        database: Connection coordinates for the external database.
        query: The SQL statement to execute (already classified + gated
            by the caller).
        parameters: Positional bind parameters.
        is_write: Whether the statement mutates the database.
        max_rows: Row cap for the returned page; one extra row is
            fetched to flag truncation.

    Returns:
        An :class:`ExternalQueryResult`.

    Raises:
        QueryError: If the driver raises while executing the statement.
            The original ``aiosqlite.Error`` is chained via ``from``.
    """
    try:
        async with _connect(database.database_path, read_only=database.read_only) as db:
            db.row_factory = aiosqlite.Row
            # Writes run in the driver's implicit transaction, committed
            # explicitly on success. On any failure (including a raise
            # while fetching a RETURNING result set) the except arm rolls
            # back deterministically rather than relying on the silent
            # rollback an uncommitted connection-close would perform.
            try:
                cursor = await db.execute(query, parameters)
                if is_write and not cursor.description:
                    rowcount = cursor.rowcount
                    await db.commit()
                    return ExternalQueryResult(
                        returned_rows=False,
                        columns=(),
                        rows=(),
                        rowcount=rowcount,
                        truncated=False,
                    )
                desc = cursor.description
                fetched = list(await cursor.fetchmany(max_rows + 1))
                await cursor.close()
                if is_write:
                    await db.commit()
            except BaseException:
                if is_write:
                    with contextlib.suppress(aiosqlite.Error):
                        await db.rollback()
                raise
            truncated = len(fetched) > max_rows
            if truncated:
                fetched = fetched[:max_rows]
            columns = tuple(str(d[0]) for d in (desc or []))
            rows = tuple(tuple(row[i] for i in range(len(columns))) for row in fetched)
            return ExternalQueryResult(
                returned_rows=True,
                columns=columns,
                rows=rows,
                rowcount=cursor.rowcount,
                truncated=truncated,
            )
    except aiosqlite.Error as exc:
        msg = "External SQL execution failed"
        raise QueryError(msg) from exc


async def list_external_tables(*, database_path: str | Path) -> tuple[str, ...]:
    """Return the table names of an external SQLite database, sorted.

    Args:
        database_path: Filesystem path to the SQLite database.

    Returns:
        Table names in ascending order.

    Raises:
        QueryError: If the driver raises while reading ``sqlite_master``.
    """
    try:
        encoded = urllib.parse.quote(str(database_path))
        async with aiosqlite.connect(f"file:{encoded}?mode=ro", uri=True) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            rows = await cursor.fetchall()
        return tuple(str(row[0]) for row in rows)
    except aiosqlite.Error as exc:
        msg = "External schema inspection failed"
        raise QueryError(msg) from exc


async def describe_external_table(
    *,
    database_path: str | Path,
    table_name: str,
) -> tuple[ExternalColumn, ...]:
    """Return column metadata for one external table via ``PRAGMA table_info``.

    The table identifier is double-quoted (defence-in-depth on top of the
    caller's identifier allowlist) so it cannot break out of the PRAGMA
    argument.

    Args:
        database_path: Filesystem path to the SQLite database.
        table_name: The table to describe.

    Returns:
        One :class:`ExternalColumn` per column, in declaration order.
        Empty when the table is unknown or has no columns.

    Raises:
        QueryError: If the driver raises while running the PRAGMA.
    """
    try:
        encoded = urllib.parse.quote(str(database_path))
        quoted = _quote_identifier(table_name)
        async with aiosqlite.connect(f"file:{encoded}?mode=ro", uri=True) as db:
            cursor = await db.execute(f"PRAGMA table_info({quoted})")
            rows = await cursor.fetchall()
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        return tuple(
            ExternalColumn(
                name=str(row[1]),
                type=str(row[2]),
                notnull=bool(row[3]),
                default=row[4],
                primary_key=bool(row[5]),
            )
            for row in rows
        )
    except aiosqlite.Error as exc:
        msg = "External schema inspection failed"
        raise QueryError(msg) from exc

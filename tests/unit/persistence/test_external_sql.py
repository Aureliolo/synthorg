"""Unit tests for the external-SQLite access adapter.

Covers the agent-facing ``sql_query`` / ``schema_inspect`` driver path:
the self-defending identifier guard, schema inspection, read/write
execution, row truncation, and the write-rollback-on-failure discipline.
"""

import sqlite3
from pathlib import Path

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.external_sql import (
    ExternalDatabase,
    describe_external_table,
    execute_external_query,
    list_external_tables,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "external.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO widgets (id, name) VALUES (1, 'alpha')")
        conn.execute("INSERT INTO widgets (id, name) VALUES (2, 'beta')")
        conn.execute("INSERT INTO widgets (id, name) VALUES (3, 'gamma')")
        conn.commit()
    finally:
        conn.close()
    return path


async def test_list_external_tables_returns_sorted_names(db_path: Path) -> None:
    assert await list_external_tables(database_path=db_path) == ("widgets",)


async def test_describe_external_table_returns_columns(db_path: Path) -> None:
    cols = await describe_external_table(database_path=db_path, table_name="widgets")
    names = tuple(c.name for c in cols)
    assert names == ("id", "name")
    name_col = next(c for c in cols if c.name == "name")
    assert name_col.notnull is True
    assert name_col.primary_key is False


async def test_describe_external_table_rejects_control_char_identifier(
    db_path: Path,
) -> None:
    # The double-quote escaping cannot neutralise control characters, so
    # the quoting chokepoint is self-defending against them.
    with pytest.raises(QueryError):
        await describe_external_table(
            database_path=db_path,
            table_name="widgets\x00; DROP TABLE widgets",
        )


async def test_describe_external_table_rejects_empty_identifier(db_path: Path) -> None:
    with pytest.raises(QueryError):
        await describe_external_table(database_path=db_path, table_name="")


async def test_execute_read_returns_rows(db_path: Path) -> None:
    result = await execute_external_query(
        ExternalDatabase(database_path=db_path),
        query="SELECT id, name FROM widgets ORDER BY id",
        parameters=(),
        is_write=False,
        max_rows=10,
    )
    assert result.returned_rows is True
    assert result.columns == ("id", "name")
    assert result.rows == ((1, "alpha"), (2, "beta"), (3, "gamma"))
    assert result.truncated is False


async def test_execute_read_flags_truncation(db_path: Path) -> None:
    result = await execute_external_query(
        ExternalDatabase(database_path=db_path),
        query="SELECT id FROM widgets ORDER BY id",
        parameters=(),
        is_write=False,
        max_rows=2,
    )
    assert result.truncated is True
    assert len(result.rows) == 2


async def test_execute_write_commits_and_reports_rowcount(db_path: Path) -> None:
    result = await execute_external_query(
        ExternalDatabase(database_path=db_path, read_only=False),
        query="UPDATE widgets SET name = 'delta' WHERE id = 1",
        parameters=(),
        is_write=True,
        max_rows=10,
    )
    assert result.returned_rows is False
    assert result.rowcount == 1
    # The write is durable: a fresh read sees the committed value.
    after = await execute_external_query(
        ExternalDatabase(database_path=db_path),
        query="SELECT name FROM widgets WHERE id = 1",
        parameters=(),
        is_write=False,
        max_rows=10,
    )
    assert after.rows == (("delta",),)


async def test_execute_write_failure_rolls_back(db_path: Path) -> None:
    # A constraint violation mid-write must roll back, leaving the row
    # untouched rather than relying on a silent connection-close rollback.
    with pytest.raises(QueryError):
        await execute_external_query(
            ExternalDatabase(database_path=db_path, read_only=False),
            query="UPDATE widgets SET name = NULL WHERE id = 1",
            parameters=(),
            is_write=True,
            max_rows=10,
        )
    after = await execute_external_query(
        ExternalDatabase(database_path=db_path),
        query="SELECT name FROM widgets WHERE id = 1",
        parameters=(),
        is_write=False,
        max_rows=10,
    )
    assert after.rows == (("alpha",),)

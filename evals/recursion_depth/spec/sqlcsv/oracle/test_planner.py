# module-kind: tests
"""R15-R18: what is refused before any row is produced.

The two exit codes are the point. A query that named something absent is a
different fault from one that could not be read at all, and a delivery that
collapses them tells an operator nothing about which end to fix.
"""

from .conftest import SqlRunner

_BAD_INPUT = 2
_NOT_FOUND = 3


def test_r15_unknown_table_exits_three(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT id FROM nowhere")

    assert result.exit_code == _NOT_FOUND
    assert result.stdout == ""
    assert result.stderr.strip() != ""


def test_r16_unknown_column_exits_three(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT nosuch FROM orders")

    assert result.exit_code == _NOT_FOUND
    assert result.stdout == ""


def test_r17_malformed_query_exits_two(run_sql: SqlRunner) -> None:
    # Against a data directory that exists, so the code reported is about the
    # statement rather than about the directory. Paired with R15 above: a
    # delivery that returns one code for both faults fails one of the two.
    result = run_sql("SELECT FROM WHERE")

    assert result.exit_code == _BAD_INPUT
    assert result.stdout == ""


def test_r18_bare_column_beside_aggregate_is_rejected(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT item, COUNT(*) FROM orders")

    assert result.exit_code == _BAD_INPUT
    assert result.stdout == ""

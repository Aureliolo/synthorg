# module-kind: tests
"""R40-R42: the command-line surface itself."""

import json

from .conftest import SqlRunner

_BAD_INPUT = 2


def test_r40_help_exits_zero(run_sql: SqlRunner) -> None:
    # No data directory and no statement: --help is answerable without either,
    # and a delivery that validates arguments first would exit 2 here.
    result = run_sql("--help", data="")

    assert result.exit_code == 0
    assert result.stdout.strip() != ""


def test_r41_missing_data_dir_exits_two(run_sql: SqlRunner) -> None:
    omitted = run_sql("SELECT id FROM orders", data="")
    absent = run_sql("SELECT id FROM orders", data="no-such-directory")

    assert omitted.exit_code == _BAD_INPUT
    assert absent.exit_code == _BAD_INPUT
    assert absent.stdout == ""


def test_r42_dash_reads_sql_from_stdin(run_sql: SqlRunner) -> None:
    result = run_sql(
        "-", fmt="json", stdin="SELECT id FROM orders WHERE item = 'doohickey'"
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == [{"id": 4}]

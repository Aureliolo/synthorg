# module-kind: tests
"""R35-R39: how a result set is rendered."""

import csv
import io
import json

from .conftest import SqlRunner


def _cells(line: str) -> list[str]:
    """Split one rendered table line into its trimmed cells.

    Returns:
        The cell values with their padding removed.
    """
    return [cell.strip() for cell in line.split("|")]


def test_r35_table_format_is_padded(run_sql: SqlRunner) -> None:
    # Default format, so this also pins what an invocation without --format
    # produces.
    result = run_sql("SELECT item, qty FROM orders ORDER BY id LIMIT 3")

    assert result.exit_code == 0, result.stderr
    lines = result.lines
    # Header, separator, three rows.
    assert len(lines) == 5
    assert _cells(lines[0]) == ["item", "qty"]
    # Non-empty AND hyphens only. `set("") <= {"-"}` is true, so the subset
    # test alone accepted a delivery that printed a blank second line.
    assert lines[1] != ""
    assert set(lines[1]) <= {"-"}
    # The separator is ` | `, spaces included. `_cells` strips its output, so
    # every assertion built on it passes for a delivery that joined the cells
    # with a bare pipe.
    assert " | " in lines[0]
    assert [_cells(line) for line in lines[2:]] == [
        ["widget", "2"],
        ["gizmo", "1"],
        ["widget", "10"],
    ]
    # Padded to the widest cell in the column: "doohickey" is absent from this
    # window, so the item column is as wide as "widget" and every item cell
    # occupies the same span.
    item_spans = {line.index("|") for line in (lines[0], *lines[2:])}
    assert len(item_spans) == 1


def test_r36_csv_format_is_rfc4180(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT id, note FROM orders WHERE id = 3", fmt="csv")

    assert result.exit_code == 0, result.stderr
    parsed = list(csv.reader(io.StringIO(result.stdout)))
    assert parsed[0] == ["id", "note"]
    # Round-tripped through a reader rather than string-matched: the
    # requirement is that a CSV reader recovers the value, not that a
    # particular quoting style was chosen.
    assert parsed[1] == ["3", "two, please"]

    # R36 names three characters that force quoting and only the comma was
    # covered. The other two are checked in the RAW output as well as through
    # a reader: a delivery that emitted an unquoted embedded quote or newline
    # produces bytes a reader silently recovers something else from.
    quoted = run_sql("SELECT note FROM orders WHERE id = 5", fmt="csv")
    assert quoted.exit_code == 0, quoted.stderr
    assert list(csv.reader(io.StringIO(quoted.stdout)))[1] == ['he said "later"']
    assert '"he said ""later"""' in quoted.stdout

    embedded = run_sql("SELECT body FROM lines WHERE id = 1", data="quoting", fmt="csv")
    assert embedded.exit_code == 0, embedded.stderr
    assert list(csv.reader(io.StringIO(embedded.stdout)))[1] == [
        "first line\nsecond line"
    ]
    assert '"first line\nsecond line"' in embedded.stdout


def test_r37_json_format_is_an_array_of_objects(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT id, item FROM orders ORDER BY id LIMIT 2", fmt="json")

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == [
        {"id": 1, "item": "widget"},
        {"id": 2, "item": "gizmo"},
    ]


def test_r38_null_rendering(run_sql: SqlRunner) -> None:
    as_csv = run_sql("SELECT qty FROM orders WHERE id = 7", fmt="csv")
    as_json = run_sql("SELECT qty FROM orders WHERE id = 7", fmt="json")

    # The exit code as well as the bytes: a delivery can render a NULL exactly
    # right and still report failure, and parsing its output says nothing
    # about the status it returned.
    assert as_csv.exit_code == 0, as_csv.stderr
    assert as_json.exit_code == 0, as_json.stderr
    assert list(csv.reader(io.StringIO(as_csv.stdout))) == [["qty"], [""]]
    assert json.loads(as_json.stdout) == [{"qty": None}]


def test_r39_empty_result_keeps_its_shape(run_sql: SqlRunner) -> None:
    # A result set with no rows is a result, not an error, and a reader piping
    # the CSV into something else still needs the header.
    query = "SELECT id FROM orders WHERE item = 'nothing'"
    as_table = run_sql(query)
    as_csv = run_sql(query, fmt="csv")
    as_json = run_sql(query, fmt="json")

    # All three formats report success. An empty result set is a result, and
    # only the table path was checking that it was reported as one: the other
    # two parsed the output, which says nothing about the status returned.
    assert as_table.exit_code == 0, as_table.stderr
    assert as_csv.exit_code == 0, as_csv.stderr
    assert as_json.exit_code == 0, as_json.stderr
    assert _cells(as_table.lines[0]) == ["id"]
    assert list(csv.reader(io.StringIO(as_csv.stdout))) == [["id"]]
    assert json.loads(as_json.stdout) == []

# module-kind: tests
"""R06-R09: what the tokeniser accepts and what it refuses."""

from .conftest import JsonRunner, SqlRunner

#: The exit code the spec assigns to input that is not usable.
_BAD_INPUT = 2


def test_r06_keywords_are_case_insensitive(json_rows: JsonRunner) -> None:
    lower = json_rows("select id from orders where id = 1")
    mixed = json_rows("SeLeCt id FrOm orders WhErE id = 1")

    assert lower == mixed == [{"id": 1}]


def test_r07_string_literals_and_escaped_quotes(json_rows: JsonRunner) -> None:
    plain = json_rows("SELECT id FROM orders WHERE item = 'gizmo' ORDER BY id LIMIT 1")
    # The doubled quote is the escape, so the literal below holds one
    # apostrophe and the statement is complete rather than unterminated.
    escaped = json_rows("SELECT id FROM orders WHERE note = 'don''t ask'")

    assert plain == [{"id": 2}]
    assert escaped == [{"id": 7}]


def test_r08_numeric_literals(json_rows: JsonRunner) -> None:
    integer = json_rows("SELECT id FROM orders WHERE qty = 10")
    decimal = json_rows("SELECT id FROM orders WHERE price = 4.25")
    negative = json_rows("SELECT id FROM orders WHERE qty > -1 ORDER BY id LIMIT 1")

    assert integer == [{"id": 3}]
    assert decimal == [{"id": 4}]
    assert negative == [{"id": 1}]


def test_r09_unterminated_string_is_an_error(run_sql: SqlRunner) -> None:
    result = run_sql("SELECT id FROM orders WHERE item = 'widget")

    assert result.exit_code == _BAD_INPUT
    assert result.stdout == ""
    assert result.stderr.strip() != ""

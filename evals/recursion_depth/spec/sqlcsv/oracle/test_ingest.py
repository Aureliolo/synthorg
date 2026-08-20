# module-kind: tests
"""R01-R05: what a CSV file means once it is read."""

from evals.recursion_depth.spec.sqlcsv.oracle.conftest import JsonRunner


def test_r01_header_row_names_the_columns(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT id, item FROM orders LIMIT 1")

    assert rows == [{"id": 1, "item": "widget"}]


def test_r02_integer_columns_sort_numerically(json_rows: JsonRunner) -> None:
    # Lexical ordering would put 10 before 2 and before 9, which is the whole
    # point: a column of digits is not a column of strings.
    rows = json_rows("SELECT label FROM counts ORDER BY n")

    assert [row["label"] for row in rows] == ["two", "nine", "ten"]


def test_r03_decimal_columns_read_as_float(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT SUM(price) AS total FROM orders WHERE item = 'widget'")

    assert rows == [{"total": 28.5}]


def test_r04_an_empty_field_is_null(json_rows: JsonRunner) -> None:
    # Distinct from zero and from the empty string: the sprocket row has no
    # quantity, and reading it as 0 would make COUNT(qty) count it.
    rows = json_rows("SELECT id FROM orders WHERE qty IS NULL")

    assert rows == [{"id": 7}]


def test_r05_quoted_fields_carry_awkward_characters(json_rows: JsonRunner) -> None:
    comma = json_rows("SELECT note FROM orders WHERE id = 3")
    quote = json_rows("SELECT note FROM orders WHERE id = 5")
    newline = json_rows("SELECT body FROM lines WHERE id = 1", data="quoting")

    assert comma == [{"note": "two, please"}]
    assert quote == [{"note": 'he said "later"'}]
    assert newline == [{"body": "first line\nsecond line"}]

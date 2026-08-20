# module-kind: tests
"""R10-R14: which statement shapes the grammar accepts."""

from evals.recursion_depth.spec.sqlcsv.oracle.conftest import JsonRunner

#: The header of the orders fixture, in declaration order.
_ORDERS_COLUMNS = ("id", "customer_id", "item", "qty", "price", "note")

#: Every id the orders fixture holds, so an assertion about "all of them" names
#: the data rather than a count nobody can check.
_ORDER_IDS = (1, 2, 3, 4, 5, 6, 7, 8)


def test_r10_select_list_from_table(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT item, qty FROM orders ORDER BY id LIMIT 2")

    assert rows == [
        {"item": "widget", "qty": 2},
        {"item": "gizmo", "qty": 1},
    ]


def test_r11_select_star_in_header_order(json_rows: JsonRunner) -> None:
    # Header order, not alphabetical and not insertion-by-accident: a reader
    # comparing two runs of the same query needs the columns in one place.
    rows = json_rows("SELECT * FROM orders ORDER BY id LIMIT 1")

    assert len(rows) == 1
    assert tuple(rows[0]) == _ORDERS_COLUMNS


def test_r12_optional_where_clause(json_rows: JsonRunner) -> None:
    # Sorted rather than compared in place: without ORDER BY the spec fixes
    # which rows come back, not which order they arrive in.
    without = json_rows("SELECT id FROM orders")
    with_where = json_rows("SELECT id FROM orders WHERE item = 'doohickey'")

    assert sorted(int(str(row["id"])) for row in without) == list(_ORDER_IDS)
    assert with_where == [{"id": 4}]


def test_r13_optional_order_by_clause(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT id FROM orders ORDER BY id DESC LIMIT 3")

    assert [row["id"] for row in rows] == [8, 7, 6]


def test_r14_optional_limit_and_offset(json_rows: JsonRunner) -> None:
    limited = json_rows("SELECT id FROM orders ORDER BY id LIMIT 2")
    offset = json_rows("SELECT id FROM orders ORDER BY id LIMIT 2 OFFSET 3")

    assert [row["id"] for row in limited] == [1, 2]
    assert [row["id"] for row in offset] == [4, 5]

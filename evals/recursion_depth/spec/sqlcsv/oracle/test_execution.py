# module-kind: tests
"""R19-R34: which rows come back, and in what order."""

import pytest

from .conftest import JsonRunner

#: Every id the orders fixture holds, so a length assertion names the data.
_ORDER_IDS = (1, 2, 3, 4, 5, 6, 7, 8)


def test_r19_projection_uses_the_select_order(json_rows: JsonRunner) -> None:
    # Reversed against the header, so a delivery projecting in header order
    # rather than in SELECT order is caught.
    rows = json_rows("SELECT qty, item FROM orders ORDER BY id LIMIT 1")

    assert tuple(rows[0]) == ("qty", "item")


def test_r20_alias_renames_the_output_column(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT item AS product FROM orders ORDER BY id LIMIT 1")

    assert rows == [{"product": "widget"}]


def test_r21_distinct_removes_duplicates(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT DISTINCT item FROM orders ORDER BY item")

    assert [row["item"] for row in rows] == ["doohickey", "gizmo", "sprocket", "widget"]


def test_r22_where_equality_and_inequality(json_rows: JsonRunner) -> None:
    equal = json_rows("SELECT id FROM orders WHERE item = 'doohickey'")
    bang = json_rows("SELECT id FROM orders WHERE item != 'gizmo' ORDER BY id")
    angle = json_rows("SELECT id FROM orders WHERE item <> 'gizmo' ORDER BY id")

    assert [row["id"] for row in equal] == [4]
    assert [row["id"] for row in bang] == [1, 3, 4, 6, 7]
    assert bang == angle


def test_r23_where_ordered_comparisons(json_rows: JsonRunner) -> None:
    # Numeric, not lexical: qty 10 is greater than qty 9, and a string
    # comparison would disagree.
    greater = json_rows("SELECT id FROM orders WHERE qty > 9 ORDER BY id")
    at_least = json_rows("SELECT id FROM orders WHERE qty >= 9 ORDER BY id")
    less = json_rows("SELECT id FROM orders WHERE price < 5 ORDER BY id")
    at_most = json_rows("SELECT id FROM orders WHERE price <= 9.50 ORDER BY id")

    assert [row["id"] for row in greater] == [3]
    assert [row["id"] for row in at_least] == [3, 5]
    assert [row["id"] for row in less] == [4, 7]
    assert [row["id"] for row in at_most] == [1, 3, 4, 6, 7]


def test_r24_where_and_binds_tighter_than_or(json_rows: JsonRunner) -> None:
    # Without the precedence rule the unparenthesised form reads as
    # (item='widget' OR item='gizmo') AND qty=1 and drops order 1.
    natural = json_rows(
        "SELECT id FROM orders "
        "WHERE item = 'widget' OR item = 'gizmo' AND qty = 1 ORDER BY id"
    )
    forced = json_rows(
        "SELECT id FROM orders "
        "WHERE (item = 'widget' OR item = 'gizmo') AND qty = 1 ORDER BY id"
    )

    assert [row["id"] for row in natural] == [1, 2, 3, 6]
    assert [row["id"] for row in forced] == [2, 6]


def test_r25_where_not(json_rows: JsonRunner) -> None:
    rows = json_rows("SELECT id FROM orders WHERE NOT item = 'gizmo' ORDER BY id")

    assert [row["id"] for row in rows] == [1, 3, 4, 6, 7]


def test_r26_where_is_null(json_rows: JsonRunner) -> None:
    missing = json_rows("SELECT id FROM orders WHERE qty IS NULL")
    present = json_rows("SELECT id FROM orders WHERE qty IS NOT NULL ORDER BY id")

    assert [row["id"] for row in missing] == [7]
    assert [row["id"] for row in present] == [1, 2, 3, 4, 5, 6, 8]


def test_r27_order_by_direction_and_ties(json_rows: JsonRunner) -> None:
    descending = json_rows("SELECT id FROM orders ORDER BY price DESC, id LIMIT 3")
    ascending = json_rows("SELECT id FROM orders ORDER BY price, id LIMIT 2")

    assert [row["id"] for row in descending] == [2, 5, 8]
    assert [row["id"] for row in ascending] == [7, 4]


def test_r28_limit_and_offset(json_rows: JsonRunner) -> None:
    # After ordering, not before: offsetting the unordered read would give a
    # different window on the same data.
    window = json_rows("SELECT id FROM orders ORDER BY id DESC LIMIT 3 OFFSET 2")

    assert [row["id"] for row in window] == [6, 5, 4]


def test_r29_count_star_and_count_column(json_rows: JsonRunner) -> None:
    # The sprocket row has no quantity, so the two counts differ by one.
    rows = json_rows("SELECT COUNT(*) AS rows_, COUNT(qty) AS quantities FROM orders")

    assert rows == [{"rows_": 8, "quantities": 7}]


def test_r30_numeric_aggregates_ignore_nulls(json_rows: JsonRunner) -> None:
    rows = json_rows(
        "SELECT SUM(qty) AS total, MIN(qty) AS lowest, MAX(qty) AS highest FROM orders"
    )
    average = json_rows("SELECT AVG(qty) AS mean FROM orders")

    assert rows == [{"total": 30, "lowest": 1, "highest": 10}]
    # Seven quantities, not eight: an implementation dividing by the row count
    # would report 3.75. Approximate because the spec fixes the value, not the
    # order the additions happen in.
    assert average[0]["mean"] == pytest.approx(30 / 7)


def test_r31_group_by_emits_one_row_per_group(json_rows: JsonRunner) -> None:
    rows = json_rows(
        "SELECT item, COUNT(*) AS n FROM orders GROUP BY item ORDER BY item"
    )

    assert rows == [
        {"item": "doohickey", "n": 1},
        {"item": "gizmo", "n": 3},
        {"item": "sprocket", "n": 1},
        {"item": "widget", "n": 3},
    ]


def test_r32_having_filters_groups(json_rows: JsonRunner) -> None:
    rows = json_rows(
        "SELECT item, COUNT(*) AS n FROM orders "
        "GROUP BY item HAVING COUNT(*) > 1 ORDER BY item"
    )

    assert [row["item"] for row in rows] == ["gizmo", "widget"]


def test_r33_inner_join_on_equality(json_rows: JsonRunner) -> None:
    # Customer 13 has orders and no customer row; customer 14 has a row and no
    # orders. An inner join drops both.
    rows = json_rows(
        "SELECT orders.id, customers.name FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.id "
        "ORDER BY orders.id"
    )

    assert [row["name"] for row in rows] == ["Ada", "Ada", "Bo", "Bo", "Cy", "Cy"]


def test_r34_left_join_fills_nulls(json_rows: JsonRunner) -> None:
    rows = json_rows(
        "SELECT orders.id, customers.name FROM orders "
        "LEFT JOIN customers ON orders.customer_id = customers.id "
        "ORDER BY orders.id"
    )

    assert len(rows) == len(_ORDER_IDS)
    assert rows[-1] == {"id": 8, "name": None}

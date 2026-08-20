# module-kind: tests
"""Plan and execute a parsed statement."""

from dataclasses import dataclass
from pathlib import Path

from sqlcsv.ast_nodes import (
    Aggregate,
    And,
    ColumnRef,
    Comparison,
    Condition,
    JoinKind,
    Literal,
    Not,
    NullTest,
    Or,
    Select,
    SelectItem,
)
from sqlcsv.errors import InputError, NotFoundError
from sqlcsv.source import Row, Value, load_table


@dataclass(frozen=True)
class Result:
    """What a statement produced.

    Attributes:
        columns: The output column names, in order.
        rows: The result rows.
    """

    columns: tuple[str, ...]
    rows: tuple[Row, ...]


def execute(statement: Select, data_dir: Path) -> Result:
    """Run *statement* against the tables in *data_dir*.

    Args:
        statement: The parsed statement.
        data_dir: Where tables are resolved.

    Returns:
        The result.

    Raises:
        InputError: The statement mixes a bare column with an aggregate and no
            GROUP BY.
        NotFoundError: A named table or column does not exist.
    """
    rows, columns = _source_rows(statement, data_dir)
    items = _resolve_items(statement, columns)
    _check_columns(statement, items, columns)
    rows = tuple(row for row in rows if _matches(statement.where, row))
    # Each output row keeps the row it came from. ORDER BY may name a column
    # the SELECT list does not project, so projecting first would sort on a
    # value that is no longer there and silently leave the rows in read order.
    if statement.group_by or _has_aggregate(items):
        paired = _aggregate_rows(statement, items, rows)
    else:
        paired = tuple((_project(row, items), row) for row in rows)
    out_columns = tuple(item.output_name for item in items)
    if statement.distinct:
        paired = _distinct(paired)
    paired = _ordered(statement, paired)
    paired = _windowed(statement, paired)
    return Result(columns=out_columns, rows=tuple(out for out, _ in paired))


def _source_rows(
    statement: Select, data_dir: Path
) -> tuple[tuple[Row, ...], tuple[str, ...]]:
    """Load the statement's source, joining when it names two tables.

    Returns:
        The source rows and the columns available to the statement.
    """
    left = load_table(data_dir, statement.table)
    if statement.join is None:
        return left.rows, left.columns
    right = load_table(data_dir, statement.join.table)
    key_left, key_right = _join_keys(statement, left.name, right.name)
    index: dict[Value, list[Row]] = {}
    for row in right.rows:
        index.setdefault(row.get(key_right), []).append(row)
    joined: list[Row] = []
    for row in left.rows:
        matches = index.get(row.get(key_left), [])
        if matches:
            joined.extend(
                {**dict.fromkeys(right.columns), **match, **row} for match in matches
            )
        elif statement.join.kind is JoinKind.LEFT:
            joined.append({**dict.fromkeys(right.columns), **row})
    return tuple(joined), tuple(dict.fromkeys((*left.columns, *right.columns)))


def _join_keys(statement: Select, left_name: str, right_name: str) -> tuple[str, str]:
    """Decide which side of the ON equality belongs to which table.

    Returns:
        The left table's key column and the right table's.
    """
    join = statement.join
    assert join is not None
    if join.left.table == right_name or join.right.table == left_name:
        return join.right.name, join.left.name
    return join.left.name, join.right.name


def _resolve_items(
    statement: Select, columns: tuple[str, ...]
) -> tuple[SelectItem, ...]:
    """Expand ``SELECT *`` into the source's columns.

    Returns:
        The select items to project.
    """
    if not statement.star:
        return statement.items
    return tuple(SelectItem(expr=ColumnRef(name=column)) for column in columns)


def _check_columns(
    statement: Select, items: tuple[SelectItem, ...], columns: tuple[str, ...]
) -> None:
    """Reject a statement naming a column no source provides.

    Raises:
        InputError: A bare column sits beside an aggregate with no GROUP BY.
        NotFoundError: A referenced column does not exist.
    """
    source = set(columns)
    # An alias makes a name available to ORDER BY and HAVING and nowhere else.
    # Counting it in the SELECT list too would make `SELECT nosuch FROM t`
    # define its own missing column and succeed.
    aliases = {item.alias for item in items if item.alias is not None}
    for ref in _projected_columns(items) + _condition_columns(statement.where):
        if ref.name not in source:
            msg = f"no column named {ref.name!r}"
            raise NotFoundError(msg)
    for ref in _derived_references(statement):
        if ref.name not in source | aliases:
            msg = f"no column named {ref.name!r}"
            raise NotFoundError(msg)
    if _has_aggregate(items) and not statement.group_by:
        bare = [item for item in items if isinstance(item.expr, ColumnRef)]
        if bare:
            msg = (
                f"column {bare[0].expr.output_name!r} sits beside an aggregate "
                "with no GROUP BY"
            )
            raise InputError(msg)


def _projected_columns(items: tuple[SelectItem, ...]) -> tuple[ColumnRef, ...]:
    """The source columns the SELECT list mentions.

    Returns:
        The referenced columns.
    """
    refs: list[ColumnRef] = []
    for item in items:
        refs.extend(_expr_columns(item.expr))
    return tuple(refs)


def _derived_references(statement: Select) -> tuple[ColumnRef, ...]:
    """The columns the clauses evaluated after projection mention.

    Returns:
        The referenced columns.
    """
    refs: list[ColumnRef] = list(statement.group_by)
    refs.extend(_condition_columns(statement.having))
    refs.extend(key.column for key in statement.order_by)
    return tuple(refs)


def _expr_columns(expr: ColumnRef | Aggregate | Literal) -> tuple[ColumnRef, ...]:
    """The columns one expression mentions.

    Returns:
        The referenced columns.
    """
    if isinstance(expr, ColumnRef):
        return (expr,)
    if isinstance(expr, Aggregate) and expr.column is not None:
        return (expr.column,)
    return ()


def _condition_columns(condition: Condition | None) -> tuple[ColumnRef, ...]:
    """The columns one condition mentions.

    Returns:
        The referenced columns.
    """
    if condition is None:
        return ()
    if isinstance(condition, Comparison):
        return _expr_columns(condition.left) + _expr_columns(condition.right)
    if isinstance(condition, NullTest):
        return (condition.column,)
    if isinstance(condition, Not):
        return _condition_columns(condition.operand)
    return _condition_columns(condition.left) + _condition_columns(condition.right)


def _has_aggregate(items: tuple[SelectItem, ...]) -> bool:
    """Whether any projected expression is an aggregate.

    Returns:
        Whether the statement aggregates.
    """
    return any(isinstance(item.expr, Aggregate) for item in items)


def _matches(condition: Condition | None, row: Row) -> bool:
    """Evaluate *condition* against one row.

    Returns:
        Whether the row satisfies it. An unknown comparison is false, which is
        what makes a NULL fail both a test and its negation.
    """
    if condition is None:
        return True
    if isinstance(condition, And):
        return _matches(condition.left, row) and _matches(condition.right, row)
    if isinstance(condition, Or):
        return _matches(condition.left, row) or _matches(condition.right, row)
    if isinstance(condition, Not):
        return not _matches(condition.operand, row)
    if isinstance(condition, NullTest):
        is_null = row.get(condition.column.name) is None
        return not is_null if condition.negated else is_null
    return _compare(condition, row)


def _compare(condition: Comparison, row: Row) -> bool:
    """Evaluate one comparison against a row.

    Returns:
        Whether it holds; false when either side is NULL.
    """
    left = _value_of(condition.left, row)
    right = _value_of(condition.right, row)
    if left is None or right is None:
        return False
    if condition.op == "=":
        return bool(left == right)
    if condition.op in ("!=", "<>"):
        return bool(left != right)
    if isinstance(left, str) != isinstance(right, str):
        return False
    if condition.op == "<":
        return bool(left < right)  # type: ignore[operator]
    if condition.op == "<=":
        return bool(left <= right)  # type: ignore[operator]
    if condition.op == ">":
        return bool(left > right)  # type: ignore[operator]
    return bool(left >= right)  # type: ignore[operator]


def _value_of(expr: ColumnRef | Aggregate | Literal, row: Row) -> Value:
    """Read one operand's value from a row.

    Returns:
        The value.
    """
    if isinstance(expr, Literal):
        return expr.value  # type: ignore[return-value]
    if isinstance(expr, ColumnRef):
        return row.get(expr.name)
    return row.get(expr.output_name)


def _project(row: Row, items: tuple[SelectItem, ...]) -> Row:
    """Build one output row in SELECT-list order.

    Returns:
        The projected row.
    """
    return {item.output_name: _value_of(item.expr, row) for item in items}


def _aggregate_rows(
    statement: Select, items: tuple[SelectItem, ...], rows: tuple[Row, ...]
) -> tuple[tuple[Row, Row], ...]:
    """Group and aggregate, then apply HAVING.

    Returns:
        One ``(output row, context row)`` pair per surviving group. The context
        carries the group keys and every aggregate under BOTH its canonical
        name and its alias, because HAVING and ORDER BY may name either and a
        context keyed only by the alias makes ``HAVING COUNT(*) > 1`` read as
        NULL and filter every group away.
    """
    keys = tuple(ref.name for ref in statement.group_by)
    groups: dict[tuple[Value, ...], list[Row]] = {}
    if keys:
        for row in rows:
            groups.setdefault(tuple(row.get(key) for key in keys), []).append(row)
    else:
        groups[()] = list(rows)
    calls = _aggregate_calls(statement, items)
    out: list[tuple[Row, Row]] = []
    for key, members in groups.items():
        context: Row = dict(zip(keys, key, strict=True))
        for call in calls:
            context[call.output_name] = _aggregate_value(call, members)
        projected: Row = {}
        for item in items:
            value = (
                context[item.expr.output_name]
                if isinstance(item.expr, Aggregate)
                else (members[0].get(item.expr.name) if members else None)
            )
            projected[item.output_name] = value
            context[item.output_name] = value
        if _matches(statement.having, context):
            out.append((projected, context))
    return tuple(out)


def _aggregate_calls(
    statement: Select, items: tuple[SelectItem, ...]
) -> tuple[Aggregate, ...]:
    """Every aggregate the statement evaluates, projected or not.

    Returns:
        The distinct aggregate calls, including any that appear only in HAVING.
    """
    calls: dict[str, Aggregate] = {}
    for item in items:
        if isinstance(item.expr, Aggregate):
            calls[item.expr.output_name] = item.expr
    for call in _condition_aggregates(statement.having):
        calls[call.output_name] = call
    return tuple(calls.values())


def _condition_aggregates(condition: Condition | None) -> tuple[Aggregate, ...]:
    """The aggregate calls one condition mentions.

    Returns:
        The referenced aggregates.
    """
    if condition is None:
        return ()
    if isinstance(condition, Comparison):
        return tuple(
            side
            for side in (condition.left, condition.right)
            if isinstance(side, Aggregate)
        )
    if isinstance(condition, NullTest):
        return ()
    if isinstance(condition, Not):
        return _condition_aggregates(condition.operand)
    return _condition_aggregates(condition.left) + _condition_aggregates(
        condition.right
    )


def _aggregate_value(call: Aggregate, members: list[Row]) -> Value:
    """Compute one aggregate over a group.

    Returns:
        The aggregate's value; ``None`` when nothing non-NULL contributed.
    """
    if call.column is None:
        return len(members)
    values = [
        row.get(call.column.name)
        for row in members
        if row.get(call.column.name) is not None
    ]
    if call.func == "count":
        return len(values)
    if not values:
        return None
    numeric = [value for value in values if isinstance(value, int | float)]
    if call.func == "sum":
        return sum(numeric)
    if call.func == "avg":
        return sum(numeric) / len(numeric)
    if call.func == "min":
        return min(values)  # type: ignore[type-var]
    return max(values)  # type: ignore[type-var]


def _distinct(paired: tuple[tuple[Row, Row], ...]) -> tuple[tuple[Row, Row], ...]:
    """Drop duplicate output rows, keeping the first of each.

    Returns:
        The deduplicated pairs.
    """
    seen: set[tuple[tuple[str, Value], ...]] = set()
    out: list[tuple[Row, Row]] = []
    for projected, context in paired:
        key = tuple(sorted(projected.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append((projected, context))
    return tuple(out)


def _ordered(
    statement: Select, paired: tuple[tuple[Row, Row], ...]
) -> tuple[tuple[Row, Row], ...]:
    """Apply ORDER BY, later keys breaking ties in earlier ones.

    Sorted on the context row, which still holds the columns the projection
    dropped, falling back to the output row for a key naming an alias.

    Returns:
        The sorted pairs.
    """
    ordered = list(paired)
    for key in reversed(statement.order_by):
        ordered.sort(
            key=lambda pair, name=key.column.name: _sort_key(  # type: ignore[misc]
                pair[1].get(name, pair[0].get(name))
            ),
            reverse=key.descending,
        )
    return tuple(ordered)


def _sort_key(value: Value) -> tuple[int, float, str]:
    """Build a total order over mixed values, NULLs first.

    Returns:
        A tuple that sorts numerics numerically and text lexically.
    """
    if value is None:
        return (0, 0.0, "")
    if isinstance(value, int | float):
        return (1, float(value), "")
    return (2, 0.0, value)


def _windowed(
    statement: Select, paired: tuple[tuple[Row, Row], ...]
) -> tuple[tuple[Row, Row], ...]:
    """Apply OFFSET then LIMIT.

    Returns:
        The windowed pairs.
    """
    start = statement.offset or 0
    if statement.limit is None:
        return paired[start:]
    return paired[start : start + statement.limit]

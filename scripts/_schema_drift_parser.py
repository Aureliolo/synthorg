"""Sibling module of ``scripts/check_schema_drift.py``: sqlglot parse layer.

Converts SQL text into the normalised dataclasses defined in
``_schema_drift_models``. Filters out non-table / non-index DDL,
collapses the SQLite boolean idiom (INTEGER + CHECK(col IN (0, 1)))
to BOOLEAN, and extracts table-level PRIMARY KEY / UNIQUE constraints.
"""

import sys
from typing import Any

import sqlglot
from sqlglot import expressions as exp
from sqlglot.expressions import DataType

if __package__ in {None, ""}:
    from _schema_drift_models import (  # type: ignore[import-not-found]
        INTEGER_TYPES_FOR_BOOLEAN_CHECK,
        NormalizedColumn,
        NormalizedIndex,
        NormalizedTable,
    )
else:
    from ._schema_drift_models import (
        INTEGER_TYPES_FOR_BOOLEAN_CHECK,
        NormalizedColumn,
        NormalizedIndex,
        NormalizedTable,
    )


def parse_schema(
    sql_text: str,
    dialect: str,
) -> tuple[dict[str, NormalizedTable], dict[str, NormalizedIndex]]:
    """Parse a ``schema.sql`` file into normalised tables and indexes.

    Statements that do not map to a CREATE TABLE / CREATE INDEX pair
    are filtered out: triggers, plpgsql functions, EndStatement
    sentinels, parser fall-back ``Command`` nodes, and ``None``
    placeholders all produce no entries.

    Args:
        sql_text: The full text of the schema file.
        dialect: Either ``"sqlite"`` or ``"postgres"``; passed through
            to sqlglot's parser.

    Returns:
        ``(tables_by_name, indexes_by_name)``. Both dicts use
        identifier strings as keys.

    Raises:
        ParseError / TokenError: If sqlglot cannot tokenise / parse
            the input. The CLI catches and translates into exit
            code 2.
    """
    tables: dict[str, NormalizedTable] = {}
    indexes: dict[str, NormalizedIndex] = {}
    parsed = sqlglot.parse(sql_text, dialect=dialect)
    for stmt in parsed:
        if stmt is None or not isinstance(stmt, exp.Create):
            continue
        kind = (stmt.kind or "").upper()
        if kind == "TABLE":
            normalised = _normalise_table(stmt, dialect)
            if normalised is not None:
                tables[normalised.name] = normalised
        elif kind == "INDEX":
            normalised_idx = _normalise_index(stmt, dialect)
            if normalised_idx is not None:
                indexes[normalised_idx.name] = normalised_idx
    return tables, indexes


def _normalise_table(stmt: exp.Create, dialect: str) -> NormalizedTable | None:
    """Convert a ``CREATE TABLE`` AST into a :class:`NormalizedTable`.

    Extracts columns plus table-level PRIMARY KEY and UNIQUE
    constraints. Table-level CHECK expressions are passed through to
    the per-column normaliser so the SQLite boolean idiom (INTEGER +
    table-level ``CHECK (col IN (0, 1))``) collapses correctly.
    """
    schema = stmt.this
    if not isinstance(schema, exp.Schema):
        print(
            f"WARNING: _normalise_table: unexpected CREATE AST shape "
            f"({type(schema).__name__}), expected Schema; skipping",
            file=sys.stderr,
        )
        return None
    table_ident = schema.this
    table_name = table_ident.name if hasattr(table_ident, "name") else str(table_ident)
    table_check_exprs = [
        child.this
        for child in schema.expressions
        if isinstance(child, exp.CheckColumnConstraint) and child.this is not None
    ]
    columns: dict[str, NormalizedColumn] = {}
    column_pk: list[str] = []
    column_uniques: set[tuple[str, ...]] = set()
    for child in schema.expressions:
        if isinstance(child, exp.ColumnDef):
            normalised_col = _normalise_column(child, dialect, table_check_exprs)
            if normalised_col is not None:
                columns[normalised_col.name] = normalised_col
                for c in child.args.get("constraints") or []:
                    if isinstance(c.kind, exp.PrimaryKeyColumnConstraint):
                        column_pk.append(normalised_col.name)
                    elif isinstance(c.kind, exp.UniqueColumnConstraint):
                        column_uniques.add((normalised_col.name,))
    table_pk = _extract_table_pk(schema)
    primary_key = tuple(table_pk) if table_pk else tuple(column_pk)
    table_uniques = _extract_table_uniques(schema)
    return NormalizedTable(
        name=table_name,
        columns=columns,
        primary_key=primary_key,
        uniques=frozenset(column_uniques | table_uniques),
    )


def _extract_table_pk(schema: exp.Schema) -> list[str]:
    """Return table-level PRIMARY KEY column names, or empty list."""
    for child in schema.expressions:
        if isinstance(child, exp.PrimaryKey):
            return [
                c.name
                for c in child.expressions
                if isinstance(c, exp.Column | exp.Identifier)
            ]
    return []


def _extract_table_uniques(schema: exp.Schema) -> set[tuple[str, ...]]:
    """Return table-level UNIQUE constraints as a set of column-tuples."""
    uniques: set[tuple[str, ...]] = set()
    for child in schema.expressions:
        if isinstance(child, exp.UniqueColumnConstraint):
            cols = child.this
            if isinstance(cols, exp.Schema):
                uniques.add(
                    tuple(
                        c.name
                        for c in cols.expressions
                        if isinstance(c, exp.Column | exp.Identifier)
                    )
                )
    return uniques


def _normalise_column(
    coldef: exp.ColumnDef,
    dialect: str,
    table_check_exprs: list[Any],
) -> NormalizedColumn | None:
    """Convert a ``ColumnDef`` AST into :class:`NormalizedColumn`.

    Boolean detection: an INTEGER column carrying ``CHECK (col IN
    (0, 1))`` either as a column-level constraint OR via a sibling
    table-level CHECK expression collapses to ``BOOLEAN`` so the
    SQLite boolean idiom matches Postgres ``BOOLEAN`` columns.
    Nullability: ``True`` iff no NOT NULL constraint is present.
    """
    kind_node = coldef.args.get("kind")
    if not isinstance(kind_node, exp.DataType):
        print(
            f"WARNING: _normalise_column: column {coldef.name!r} has no "
            f"DataType node ({type(kind_node).__name__}); skipping",
            file=sys.stderr,
        )
        return None
    canonical_type = kind_node.this
    raw_type = kind_node.sql(dialect=dialect).upper()
    constraints = coldef.args.get("constraints") or []
    not_null = any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in constraints)
    is_pk = any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint) for c in constraints)
    nullable = not (not_null or is_pk)
    if canonical_type in INTEGER_TYPES_FOR_BOOLEAN_CHECK and (
        _has_boolean_check_in_column(coldef.name, constraints)
        or _has_boolean_check_in_table(coldef.name, table_check_exprs)
    ):
        canonical_type = DataType.Type.BOOLEAN
    return NormalizedColumn(
        name=coldef.name,
        canonical_type=canonical_type,
        raw_type=raw_type,
        nullable=nullable,
    )


def _has_boolean_check_in_column(
    column_name: str,
    constraints: list[exp.ColumnConstraint],
) -> bool:
    """Return True if any column constraint encodes ``IN (0, 1)`` for *column_name*."""
    for c in constraints:
        if isinstance(c.kind, exp.CheckColumnConstraint) and _is_zero_one_in_check(
            c.kind.this, column_name
        ):
            return True
    return False


def _has_boolean_check_in_table(
    column_name: str,
    table_check_exprs: list[Any],
) -> bool:
    """Return True if any table-level CHECK encodes ``IN (0, 1)`` for *column_name*."""
    return any(_is_zero_one_in_check(expr, column_name) for expr in table_check_exprs)


def _is_zero_one_in_check(expr: Any, column_name: str) -> bool:
    """Return True iff *expr* is the AST shape ``column_name IN (0, 1)``.

    Order of values is irrelevant: ``IN (1, 0)`` matches too. String
    literals (``IN ('0', '1')``) do NOT match: that is a TEXT-stored
    flag, not a SQLite boolean idiom.
    """
    if not isinstance(expr, exp.In):
        return False
    target = expr.this
    if not isinstance(target, exp.Column):
        return False
    if target.name != column_name:
        return False
    values = {
        literal.this
        for literal in expr.expressions
        if isinstance(literal, exp.Literal) and not literal.is_string
    }
    return values == {"0", "1"}


def _normalise_index(stmt: exp.Create, dialect: str) -> NormalizedIndex | None:
    """Convert a ``CREATE INDEX`` AST into a :class:`NormalizedIndex`."""
    inner = stmt.this
    if not isinstance(inner, exp.Index):
        return None
    name_ident = inner.args.get("this")
    if name_ident is None:
        print(
            "WARNING: _normalise_index: index has no name node; skipping",
            file=sys.stderr,
        )
        return None
    name = name_ident.name if hasattr(name_ident, "name") else str(name_ident)
    table_node = inner.args.get("table")
    table_name = ""
    if table_node is not None and hasattr(table_node, "name"):
        table_name = table_node.name
    unique = bool(stmt.args.get("unique"))
    columns, where_text, using_text = _extract_index_params(inner, dialect)
    return NormalizedIndex(
        name=name,
        table=table_name,
        columns=columns,
        unique=unique,
        where=where_text,
        using=using_text,
    )


def _extract_index_params(
    inner: exp.Index,
    dialect: str,
) -> tuple[tuple[str, ...], str | None, str | None]:
    """Pull (columns, where, using) out of an ``IndexParameters`` node."""
    params = inner.args.get("params")
    columns: tuple[str, ...] = ()
    where_text: str | None = None
    using_text: str | None = None
    if params is None:
        return columns, where_text, using_text
    ordered_cols = params.args.get("columns") or []
    column_names: list[str] = []
    for ordered in ordered_cols:
        target = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if isinstance(target, exp.Column):
            column_names.append(target.name)
    columns = tuple(column_names)
    where_node = params.args.get("where")
    if where_node is not None:
        where_text = where_node.this.sql(dialect=dialect)
    using_node = params.args.get("using")
    if using_node is not None:
        using_text = using_node.sql(dialect=dialect).upper()
    return columns, where_text, using_text

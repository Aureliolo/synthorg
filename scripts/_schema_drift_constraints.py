"""Sibling module of ``scripts/check_schema_drift.py``: constraint extraction.

Column shapes are only half of what a table promises. The other half is what it
refuses (a CHECK), what it writes when nobody says (a DEFAULT), and what a
delete does to the rows pointing at it (a foreign-key action). All three are
reproduced by hand whenever SQLite rebuilds a table, and all three are
invisible to a comparison of name, type, nullability, key and index alone: a
CHECK dropped from a rebuilt table, or ``ON DELETE CASCADE`` retyped as the
default ``NO ACTION``, leaves two schemas that compare identical and behave
differently.

Everything here renders through sqlglot's default dialect so a SQLite tree and
a Postgres tree of the same logical constraint produce the same text, with the
handful of genuine dialect spellings folded to one form first.
"""

import re
import sys
from typing import Final

from sqlglot import expressions as exp

if __package__ in {None, ""}:
    from _schema_drift_models import (  # type: ignore[import-not-found]
        NormalizedForeignKey,
    )
else:
    from ._schema_drift_models import NormalizedForeignKey

#: What the standard says an unstated referential action is.
NO_ACTION: Final[str] = "NO ACTION"

#: Default expressions that name the same value in each dialect's own words.
#: ``NOW()`` and SQLite's ``strftime`` incantation both mean "when the row was
#: written", and TRUE/FALSE are what Postgres writes where SQLite writes 1/0.
_DEFAULT_SYNONYMS: Final[dict[str, str]] = {
    "NOW()": "CURRENT_TIMESTAMP",
    "CURRENT_TIMESTAMP()": "CURRENT_TIMESTAMP",
    "TRUE": "1",
    "FALSE": "0",
}

#: SQLite has no ``now()``, so it spells the same default as a strftime over
#: the literal ``'now'``. Matched on that literal rather than on the format
#: string, which each table is free to choose.
_SQLITE_NOW: Final[re.Pattern[str]] = re.compile(r"\bCAST\('NOW' AS TIMESTAMP\)")


def _strip_casts(node: exp.Expression) -> exp.Expression:
    """Drop every ``CAST(x AS t)`` wrapper, keeping the value inside.

    A Postgres default writes its type (``'{}'::jsonb``); SQLite stores the
    identical literal in a TEXT column and cannot write one. The cast is how a
    dialect says what it already declared on the column, so it is a spelling.

    Returns:
        The expression with casts folded away.
    """

    def _fold(candidate: exp.Expression) -> exp.Expression:
        if isinstance(candidate, exp.Cast) and candidate.this is not None:
            inner: exp.Expression = candidate.this
            return inner
        return candidate

    folded: exp.Expression = node.copy().transform(_fold)
    return folded


def canonical_default(node: exp.Expression) -> str:
    """Render a DEFAULT expression so both dialects spell it the same way.

    Returns:
        The canonical text.
    """
    text = _strip_casts(node).sql().strip().upper()
    if _SQLITE_NOW.search(node.sql().upper()):
        return "CURRENT_TIMESTAMP"
    return _DEFAULT_SYNONYMS.get(text, text)


def canonical_check(node: exp.Expression) -> str:
    """Render a CHECK expression in one dialect, whitespace collapsed.

    Returns:
        The canonical text.
    """
    return re.sub(r"\s+", " ", node.sql()).strip().upper()


def column_default(coldef: exp.ColumnDef) -> str | None:
    """The canonical DEFAULT of *coldef*, or ``None`` when it declares none.

    Returns:
        The canonical default text, or ``None``.
    """
    for constraint in coldef.args.get("constraints") or []:
        kind = constraint.kind
        if isinstance(kind, exp.DefaultColumnConstraint) and kind.this is not None:
            return canonical_default(kind.this)
    return None


def _check_expressions(schema: exp.Schema) -> list[exp.Expression]:
    """Every CHECK in a CREATE TABLE, table-level and column-level alike.

    Returns:
        The expressions, in declaration order.
    """
    found: list[exp.Expression] = []
    for child in schema.expressions:
        if isinstance(child, exp.CheckColumnConstraint) and child.this is not None:
            found.append(child.this)
        elif isinstance(child, exp.ColumnDef):
            found.extend(
                constraint.kind.this
                for constraint in child.args.get("constraints") or []
                if isinstance(constraint.kind, exp.CheckColumnConstraint)
                and constraint.kind.this is not None
            )
    return found


def table_checks(
    schema: exp.Schema,
    boolean_columns: frozenset[str],
) -> frozenset[str]:
    """Canonical CHECK text for a table, minus the SQLite boolean idiom.

    A ``CHECK (flag IN (0, 1))`` beside an INTEGER column is how SQLite spells
    a boolean; the column normaliser has already turned that pair into
    ``BOOLEAN``, which is what Postgres declares natively with no CHECK at all.
    Comparing it here would report every such column as a one-sided constraint.

    Args:
        schema: The CREATE TABLE's schema node.
        boolean_columns: Columns the normaliser collapsed to BOOLEAN.

    Returns:
        The canonical expressions.
    """
    return frozenset(
        canonical_check(expr)
        for expr in _check_expressions(schema)
        if not _is_boolean_idiom(expr, boolean_columns)
    )


def _is_boolean_idiom(expr: exp.Expression, boolean_columns: frozenset[str]) -> bool:
    """Return True iff *expr* is the ``IN (0, 1)`` check of a collapsed column."""
    if not isinstance(expr, exp.In):
        return False
    target = expr.this
    return isinstance(target, exp.Column) and target.name in boolean_columns


#: ``ON DELETE CASCADE`` and its siblings, wherever they are written down.
#: sqlglot keeps them as raw option strings on the reference rather than as
#: parsed nodes, and pg_dump writes them into an ALTER, so one pattern reads
#: both and neither caller invents its own spelling of the same clause.
FK_ACTION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"ON\s+(DELETE|UPDATE)\s+"
    r"(CASCADE|RESTRICT|SET\s+NULL|SET\s+DEFAULT|NO\s+ACTION)",
    re.IGNORECASE,
)


def referential_actions(text: str) -> dict[str, str]:
    """The ``ON DELETE`` / ``ON UPDATE`` actions stated in *text*.

    Returns:
        Event name to canonical action, for the events actually stated.
    """
    return {
        event.upper(): re.sub(r"\s+", " ", action).strip().upper()
        for event, action in FK_ACTION_PATTERN.findall(text)
    }


def _reference_target(reference: exp.Reference) -> tuple[str, tuple[str, ...]]:
    """The table and columns a REFERENCES clause points at.

    Returns:
        ``(table_name, column_names)``; the columns are empty when the clause
        names none, which the standard reads as the referenced primary key.
    """
    inner = reference.this
    if isinstance(inner, exp.Schema):
        table = inner.this
        return (
            table.name if hasattr(table, "name") else str(table),
            tuple(
                column.name
                for column in inner.expressions
                if isinstance(column, exp.Column | exp.Identifier)
            ),
        )
    return (inner.name if hasattr(inner, "name") else str(inner)), ()


def _actions_of(reference: exp.Reference) -> tuple[str, str]:
    """The ``(on_delete, on_update)`` a REFERENCES clause declares.

    Read from the whole rendered clause: sqlglot keeps the actions as raw
    option strings rather than as ``delete`` / ``update`` args, so reading
    those args alone returns the standard default for every reference and the
    comparison then agrees with itself on every table.

    Returns:
        Both actions, canonicalised, defaulted when unstated.
    """
    stated = referential_actions(reference.sql())
    return (
        stated.get("DELETE", NO_ACTION),
        stated.get("UPDATE", NO_ACTION),
    )


def _column_level_foreign_key(coldef: exp.ColumnDef) -> NormalizedForeignKey | None:
    """The reference *coldef* declares inline, or ``None``.

    Returns:
        The normalised reference, or ``None`` when the column declares none.
    """
    for constraint in coldef.args.get("constraints") or []:
        kind = constraint.kind
        if not isinstance(kind, exp.Reference):
            continue
        ref_table, ref_columns = _reference_target(kind)
        on_delete, on_update = _actions_of(kind)
        return NormalizedForeignKey(
            columns=(coldef.name,),
            ref_table=ref_table,
            ref_columns=ref_columns,
            on_delete=on_delete,
            on_update=on_update,
        )
    return None


def _table_level_foreign_key(node: exp.ForeignKey) -> NormalizedForeignKey | None:
    """A table-level ``FOREIGN KEY (...) REFERENCES ...`` clause.

    Returns:
        The normalised reference, or ``None`` when it names no target.
    """
    reference = node.args.get("reference")
    if not isinstance(reference, exp.Reference):
        print(
            "WARNING: _table_level_foreign_key: clause has no REFERENCES node; "
            "skipping",
            file=sys.stderr,
        )
        return None
    ref_table, ref_columns = _reference_target(reference)
    on_delete, on_update = _actions_of(reference)
    return NormalizedForeignKey(
        columns=tuple(
            column.name
            for column in node.expressions
            if isinstance(column, exp.Column | exp.Identifier)
        ),
        ref_table=ref_table,
        ref_columns=ref_columns,
        on_delete=on_delete,
        on_update=on_update,
    )


def table_foreign_keys(schema: exp.Schema) -> frozenset[NormalizedForeignKey]:
    """Every reference a table declares, inline or as a table-level clause.

    Returns:
        The normalised references.
    """
    found: set[NormalizedForeignKey] = set()
    for child in schema.expressions:
        if isinstance(child, exp.ForeignKey):
            table_level = _table_level_foreign_key(child)
            if table_level is not None:
                found.add(table_level)
        elif isinstance(child, exp.ColumnDef):
            inline = _column_level_foreign_key(child)
            if inline is not None:
                found.add(inline)
    return frozenset(found)

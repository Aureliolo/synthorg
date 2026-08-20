# module-kind: tests
"""The shapes a parsed statement takes."""

from dataclasses import dataclass, field
from enum import StrEnum


class JoinKind(StrEnum):
    """How a second source is attached."""

    INNER = "inner"
    LEFT = "left"


@dataclass(frozen=True)
class ColumnRef:
    """A column, optionally qualified by its table.

    Attributes:
        name: The bare column name.
        table: The qualifying table, when the reference carried one.
    """

    name: str
    table: str | None = None

    @property
    def output_name(self) -> str:
        """What this reference is called in the output.

        Returns:
            The bare column name.
        """
        return self.name


@dataclass(frozen=True)
class Literal:
    """A constant.

    Attributes:
        value: The decoded value.
    """

    value: object


@dataclass(frozen=True)
class Aggregate:
    """An aggregate call.

    Attributes:
        func: The function name, lowercased.
        column: The argument, or ``None`` for ``COUNT(*)``.
    """

    func: str
    column: ColumnRef | None

    @property
    def output_name(self) -> str:
        """What this call is called in the output.

        Returns:
            The rendered call text.
        """
        inner = "*" if self.column is None else self.column.name
        return f"{self.func.upper()}({inner})"


@dataclass(frozen=True)
class SelectItem:
    """One entry in the SELECT list.

    Attributes:
        expr: What is projected.
        alias: The AS name, when one was given.
    """

    expr: ColumnRef | Aggregate
    alias: str | None = None

    @property
    def output_name(self) -> str:
        """The output column name.

        Returns:
            The alias when present, else the expression's own name.
        """
        return self.alias if self.alias is not None else self.expr.output_name


@dataclass(frozen=True)
class Comparison:
    """A binary comparison.

    Attributes:
        left: The left operand.
        op: The operator text.
        right: The right operand.
    """

    left: ColumnRef | Aggregate | Literal
    op: str
    right: ColumnRef | Aggregate | Literal


@dataclass(frozen=True)
class NullTest:
    """An ``IS NULL`` / ``IS NOT NULL`` test.

    Attributes:
        column: The column tested.
        negated: Whether the test is ``IS NOT NULL``.
    """

    column: ColumnRef
    negated: bool


@dataclass(frozen=True)
class Not:
    """A negation.

    Attributes:
        operand: What is negated.
    """

    operand: Condition


@dataclass(frozen=True)
class And:
    """A conjunction.

    Attributes:
        left: The left operand.
        right: The right operand.
    """

    left: Condition
    right: Condition


@dataclass(frozen=True)
class Or:
    """A disjunction.

    Attributes:
        left: The left operand.
        right: The right operand.
    """

    left: Condition
    right: Condition


Condition = Comparison | NullTest | Not | And | Or


@dataclass(frozen=True)
class Join:
    """A joined source.

    Attributes:
        kind: Inner or left.
        table: The table joined in.
        left: The left side of the ON equality.
        right: The right side of the ON equality.
    """

    kind: JoinKind
    table: str
    left: ColumnRef
    right: ColumnRef


@dataclass(frozen=True)
class OrderKey:
    """One sort key.

    Attributes:
        column: The column sorted on.
        descending: Whether the sort is descending.
    """

    column: ColumnRef
    descending: bool = False


@dataclass(frozen=True)
class Select:
    """A parsed statement.

    Attributes:
        items: The SELECT list, empty for ``SELECT *``.
        star: Whether the list was ``*``.
        distinct: Whether duplicates are removed.
        table: The primary source.
        join: The joined source, when one was given.
        where: The row filter.
        group_by: The grouping columns.
        having: The group filter.
        order_by: The sort keys.
        limit: The row cap.
        offset: Rows skipped before the cap applies.
    """

    table: str
    items: tuple[SelectItem, ...] = ()
    star: bool = False
    distinct: bool = False
    join: Join | None = None
    where: Condition | None = None
    group_by: tuple[ColumnRef, ...] = field(default=())
    having: Condition | None = None
    order_by: tuple[OrderKey, ...] = field(default=())
    limit: int | None = None
    offset: int | None = None

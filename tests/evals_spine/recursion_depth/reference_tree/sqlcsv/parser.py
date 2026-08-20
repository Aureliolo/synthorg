# module-kind: tests
"""Turn tokens into a statement."""

from typing import Final

from sqlcsv.ast_nodes import (
    Aggregate,
    And,
    ColumnRef,
    Comparison,
    Condition,
    Join,
    JoinKind,
    Literal,
    Not,
    NullTest,
    Or,
    OrderKey,
    Select,
    SelectItem,
)
from sqlcsv.errors import InputError
from sqlcsv.lexer import Token, TokenKind, tokenize

_AGGREGATES: Final[frozenset[str]] = frozenset({"count", "sum", "avg", "min", "max"})
_COMPARISONS: Final[frozenset[str]] = frozenset({"=", "!=", "<>", "<", "<=", ">", ">="})


class Parser:
    """A recursive-descent parser over a token list.

    Args:
        tokens: The lexed statement.
    """

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def parse(self) -> Select:
        """Parse one complete statement.

        Returns:
            The parsed :class:`Select`.

        Raises:
            InputError: The statement does not match the grammar.
        """
        self._expect_keyword("select")
        distinct = self._accept_keyword("distinct")
        star = False
        items: tuple[SelectItem, ...] = ()
        if self._accept_symbol("*"):
            star = True
        else:
            items = self._parse_select_list()
        self._expect_keyword("from")
        table = self._expect_identifier()
        join = self._parse_join()
        where = self._parse_condition_clause("where")
        group_by = self._parse_group_by()
        having = self._parse_condition_clause("having")
        order_by = self._parse_order_by()
        limit, offset = self._parse_limit()
        if self._peek().kind is not TokenKind.END:
            msg = f"unexpected trailing input at {self._peek().text!r}"
            raise InputError(msg)
        return Select(
            table=table,
            items=items,
            star=star,
            distinct=distinct,
            join=join,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )

    def _parse_select_list(self) -> tuple[SelectItem, ...]:
        """Parse one or more projected expressions.

        Returns:
            The select items.
        """
        items = [self._parse_select_item()]
        while self._accept_symbol(","):
            items.append(self._parse_select_item())
        return tuple(items)

    def _parse_select_item(self) -> SelectItem:
        """Parse one projected expression and its optional alias.

        Returns:
            The select item.
        """
        expr = self._parse_projection()
        alias = self._expect_identifier() if self._accept_keyword("as") else None
        return SelectItem(expr=expr, alias=alias)

    def _parse_projection(self) -> ColumnRef | Aggregate:
        """Parse a column reference or an aggregate call.

        Returns:
            The projected expression.

        Raises:
            InputError: The aggregate call is malformed.
        """
        token = self._peek()
        if (
            token.kind is TokenKind.IDENT
            and token.text.lower() in _AGGREGATES
            and self._peek(1).text == "("
        ):
            func = token.text.lower()
            self._advance()
            self._expect_symbol("(")
            column = None if self._accept_symbol("*") else self._parse_column_ref()
            self._expect_symbol(")")
            if func != "count" and column is None:
                msg = f"{func.upper()}(*) is not a valid aggregate"
                raise InputError(msg)
            return Aggregate(func=func, column=column)
        return self._parse_column_ref()

    def _parse_column_ref(self) -> ColumnRef:
        """Parse a possibly qualified column reference.

        Returns:
            The column reference.
        """
        first = self._expect_identifier()
        if self._accept_symbol("."):
            return ColumnRef(name=self._expect_identifier(), table=first)
        return ColumnRef(name=first)

    def _parse_join(self) -> Join | None:
        """Parse an optional INNER or LEFT join.

        Returns:
            The join, or ``None`` when the statement has one source.
        """
        if self._accept_keyword("inner"):
            kind = JoinKind.INNER
        elif self._accept_keyword("left"):
            kind = JoinKind.LEFT
        else:
            return None
        self._expect_keyword("join")
        table = self._expect_identifier()
        self._expect_keyword("on")
        left = self._parse_column_ref()
        self._expect_symbol("=")
        right = self._parse_column_ref()
        return Join(kind=kind, table=table, left=left, right=right)

    def _parse_condition_clause(self, keyword: str) -> Condition | None:
        """Parse an optional condition introduced by *keyword*.

        Returns:
            The condition, or ``None`` when the clause is absent.
        """
        if not self._accept_keyword(keyword):
            return None
        return self._parse_or()

    def _parse_or(self) -> Condition:
        """Parse a disjunction, the loosest-binding level.

        Returns:
            The condition.
        """
        left = self._parse_and()
        while self._accept_keyword("or"):
            left = Or(left=left, right=self._parse_and())
        return left

    def _parse_and(self) -> Condition:
        """Parse a conjunction, binding tighter than OR.

        Returns:
            The condition.
        """
        left = self._parse_unary()
        while self._accept_keyword("and"):
            left = And(left=left, right=self._parse_unary())
        return left

    def _parse_unary(self) -> Condition:
        """Parse a negation, a parenthesised group, or a leaf test.

        Returns:
            The condition.
        """
        if self._accept_keyword("not"):
            return Not(operand=self._parse_unary())
        if self._accept_symbol("("):
            inner = self._parse_or()
            self._expect_symbol(")")
            return inner
        return self._parse_predicate()

    def _parse_predicate(self) -> Condition:
        """Parse a comparison or a null test.

        Returns:
            The condition.

        Raises:
            InputError: The operator is not one the language has.
        """
        left = self._parse_operand()
        if self._accept_keyword("is"):
            negated = self._accept_keyword("not")
            self._expect_keyword("null")
            if not isinstance(left, ColumnRef):
                msg = "IS NULL applies to a column"
                raise InputError(msg)
            return NullTest(column=left, negated=negated)
        token = self._peek()
        if token.kind is not TokenKind.SYMBOL or token.text not in _COMPARISONS:
            msg = f"expected a comparison operator, got {token.text!r}"
            raise InputError(msg)
        self._advance()
        return Comparison(left=left, op=token.text, right=self._parse_operand())

    def _parse_operand(self) -> ColumnRef | Aggregate | Literal:
        """Parse one side of a comparison.

        Returns:
            The operand.
        """
        token = self._peek()
        if token.kind in (TokenKind.NUMBER, TokenKind.STRING):
            self._advance()
            return Literal(value=token.value)
        if token.kind is TokenKind.KEYWORD and token.text == "null":
            self._advance()
            return Literal(value=None)
        return self._parse_projection()

    def _parse_group_by(self) -> tuple[ColumnRef, ...]:
        """Parse an optional GROUP BY.

        Returns:
            The grouping columns, empty when the clause is absent.
        """
        if not self._accept_keyword("group"):
            return ()
        self._expect_keyword("by")
        columns = [self._parse_column_ref()]
        while self._accept_symbol(","):
            columns.append(self._parse_column_ref())
        return tuple(columns)

    def _parse_order_by(self) -> tuple[OrderKey, ...]:
        """Parse an optional ORDER BY.

        Returns:
            The sort keys, empty when the clause is absent.
        """
        if not self._accept_keyword("order"):
            return ()
        self._expect_keyword("by")
        keys = [self._parse_order_key()]
        while self._accept_symbol(","):
            keys.append(self._parse_order_key())
        return tuple(keys)

    def _parse_order_key(self) -> OrderKey:
        """Parse one sort key and its direction.

        Returns:
            The sort key.
        """
        column = self._parse_column_ref()
        if self._accept_keyword("desc"):
            return OrderKey(column=column, descending=True)
        self._accept_keyword("asc")
        return OrderKey(column=column)

    def _parse_limit(self) -> tuple[int | None, int | None]:
        """Parse an optional LIMIT and OFFSET.

        Returns:
            The limit and the offset, either possibly ``None``.
        """
        if not self._accept_keyword("limit"):
            return None, None
        limit = self._expect_integer()
        offset = self._expect_integer() if self._accept_keyword("offset") else None
        return limit, offset

    def _peek(self, ahead: int = 0) -> Token:
        """Look at a token without consuming it.

        Returns:
            The token, or the END token past the end.
        """
        index = self._index + ahead
        if index >= len(self._tokens):
            return self._tokens[-1]
        return self._tokens[index]

    def _advance(self) -> Token:
        """Consume and return the current token.

        Returns:
            The consumed token.
        """
        token = self._peek()
        self._index += 1
        return token

    def _accept_keyword(self, keyword: str) -> bool:
        """Consume *keyword* if it is next.

        Returns:
            Whether it was consumed.
        """
        token = self._peek()
        if token.kind is TokenKind.KEYWORD and token.text == keyword:
            self._advance()
            return True
        return False

    def _accept_symbol(self, symbol: str) -> bool:
        """Consume *symbol* if it is next.

        Returns:
            Whether it was consumed.
        """
        token = self._peek()
        if token.kind is TokenKind.SYMBOL and token.text == symbol:
            self._advance()
            return True
        return False

    def _expect_keyword(self, keyword: str) -> None:
        """Consume *keyword* or fail.

        Raises:
            InputError: The next token is something else.
        """
        if not self._accept_keyword(keyword):
            msg = f"expected {keyword.upper()}, got {self._peek().text!r}"
            raise InputError(msg)

    def _expect_symbol(self, symbol: str) -> None:
        """Consume *symbol* or fail.

        Raises:
            InputError: The next token is something else.
        """
        if not self._accept_symbol(symbol):
            msg = f"expected {symbol!r}, got {self._peek().text!r}"
            raise InputError(msg)

    def _expect_identifier(self) -> str:
        """Consume an identifier or fail.

        Returns:
            The identifier text.

        Raises:
            InputError: The next token is not an identifier.
        """
        token = self._peek()
        if token.kind is not TokenKind.IDENT:
            msg = f"expected a name, got {token.text!r}"
            raise InputError(msg)
        self._advance()
        return token.text

    def _expect_integer(self) -> int:
        """Consume a non-negative integer literal or fail.

        Returns:
            The integer.

        Raises:
            InputError: The next token is not an integer literal.
        """
        token = self._peek()
        if token.kind is not TokenKind.NUMBER or not isinstance(token.value, int):
            msg = f"expected an integer, got {token.text!r}"
            raise InputError(msg)
        self._advance()
        return token.value


def parse(source: str) -> Select:
    """Lex and parse *source*.

    Args:
        source: The statement text.

    Returns:
        The parsed statement.

    Raises:
        InputError: The statement is not usable.
    """
    return Parser(tokenize(source)).parse()

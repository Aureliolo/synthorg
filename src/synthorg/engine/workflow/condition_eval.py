"""Safe condition expression evaluator for workflow conditional nodes.

Supports a minimal expression language without ``eval()`` or
``exec()``.  Designed to be replaceable with a richer evaluator
in a future iteration.

Supported expressions:

- Boolean literals: ``"true"``, ``"false"`` (case-insensitive)
- Key lookup (truthy): ``"has_budget"`` -- returns ``bool(context.get(key))``
  Key lookup on a missing key returns ``False``
  (``context.get(key)`` is ``None``, which is falsy).
- Equality: ``"priority == high"`` -- returns ``context[key] == value``
- Inequality: ``"env != prod"`` -- returns ``context[key] != value``
- Missing keys return ``False`` (equality) or ``True`` (inequality)
- Empty or whitespace-only expressions evaluate to ``False``
- Compound operators: ``AND``, ``OR``, ``NOT`` (case-insensitive)
- Parenthesized groups: ``(expr1 AND expr2) OR expr3``

Operator precedence (highest to lowest): NOT, AND, OR.

.. note:: Context keys literally named ``AND``, ``OR``, or ``NOT``
   (case-insensitive) collide with operators in compound expressions.
   When the entire expression is exactly one of these keywords it is
   treated as a simple key lookup, but comparisons like
   ``status == OR`` or values containing operator words
   (``title == Research AND Development``) are **not** supported.
   Use values that do not contain operator keywords, or restructure
   the expression to avoid the ambiguity.

This function aims to never raise -- all edge cases (empty
expressions, missing keys, malformed comparisons) resolve to a
boolean.  Parse errors are logged at WARNING level.
"""

import re
from collections.abc import Mapping
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.condition_eval import (
    CONDITION_EVAL_PARSE_ERROR,
)

logger = get_logger(__name__)

# Maximum number of tokens accepted before rejecting as too complex.
_MAX_TOKEN_COUNT: Final[int] = 256


def _eval_comparison(
    expr: str,
    context: Mapping[str, object],
) -> bool | None:
    """Evaluate equality/inequality comparisons.

    Returns:
        The boolean result of the comparison, or ``None`` if
        ``expr`` contains neither ``==`` nor ``!=``.
    """
    for op in ("!=", "=="):
        if op not in expr:
            continue
        key, _, value = expr.partition(op)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return False
        ctx_value = context.get(key)
        missing = key not in context
        if op == "!=":
            return True if missing else str(ctx_value) != value
        return False if missing else str(ctx_value) == value
    return None


def _eval_atom(
    expr: str,
    context: Mapping[str, object],
) -> bool:
    """Evaluate a single atomic expression (no compound operators).

    Handles boolean literals, comparisons, and key lookups.

    Returns:
        ``True`` / ``False`` per the evaluation; an empty or
        unrecognised expression resolves to ``False``.
    """
    expr = expr.strip()
    if not expr:
        return False

    lower = expr.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False

    result = _eval_comparison(expr, context)
    if result is not None:
        return result

    return bool(context.get(expr))


# ── Tokenizer ────────────────────────────────────────────────────

# Match standalone AND/OR/NOT keywords (word boundaries) and parens.
_KEYWORD_RE = re.compile(
    r"""
    (?P<paren>[()]) |
    \b(?P<keyword>AND|OR|NOT)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _tokenize(expression: str) -> list[str]:
    """Tokenize a condition expression into operators, parens, and atoms.

    Keywords (AND/OR/NOT) are only recognized at word boundaries,
    so ``ORLANDO`` or ``NOTICE`` are not split.

    Returns:
        The ordered list of tokens (keywords upper-cased, atoms
        stripped of surrounding whitespace).
    """
    tokens: list[str] = []
    pos = 0
    for match in _KEYWORD_RE.finditer(expression):
        start = match.start()
        # Collect any atom text before this token
        if start > pos:
            atom = expression[pos:start].strip()
            if atom:
                tokens.append(atom)
        paren = match.group("paren")
        keyword = match.group("keyword")
        if paren:
            tokens.append(paren)
        elif keyword:
            tokens.append(keyword.upper())
        pos = match.end()
    # Trailing atom text
    if pos < len(expression):
        atom = expression[pos:].strip()
        if atom:
            tokens.append(atom)
    return tokens


# ── Recursive descent parser ─────────────────────────────────────


def _parse_or(
    tokens: list[str],
    pos: int,
    context: Mapping[str, object],
) -> tuple[bool, int]:
    """Parse OR-level (lowest precedence).

    Returns:
        ``(value, new_pos)``: the boolean result of the OR-level
        expression and the cursor position after the last consumed
        token.
    """
    left, pos = _parse_and(tokens, pos, context)
    while pos < len(tokens) and tokens[pos] == "OR":
        pos += 1  # consume OR
        right, pos = _parse_and(tokens, pos, context)
        left = left or right
    return left, pos


def _parse_and(
    tokens: list[str],
    pos: int,
    context: Mapping[str, object],
) -> tuple[bool, int]:
    """Parse AND-level (higher precedence than OR).

    Returns:
        ``(value, new_pos)``: the boolean result of the AND-level
        expression and the cursor position after the last consumed
        token.
    """
    left, pos = _parse_not(tokens, pos, context)
    while pos < len(tokens) and tokens[pos] == "AND":
        pos += 1  # consume AND
        right, pos = _parse_not(tokens, pos, context)
        left = left and right
    return left, pos


def _parse_not(
    tokens: list[str],
    pos: int,
    context: Mapping[str, object],
) -> tuple[bool, int]:
    """Parse NOT-level (highest precedence).

    Returns:
        ``(value, new_pos)``: the boolean result of the NOT-level
        expression (or the inner atom when ``NOT`` is absent) and the
        cursor position after the last consumed token.
    """
    if pos < len(tokens) and tokens[pos] == "NOT":
        pos += 1  # consume NOT
        value, pos = _parse_not(tokens, pos, context)
        return not value, pos
    return _parse_atom_token(tokens, pos, context)


def _parse_atom_token(
    tokens: list[str],
    pos: int,
    context: Mapping[str, object],
) -> tuple[bool, int]:
    """Parse an atom: parenthesized group or single comparison.

    Returns:
        ``(value, new_pos)``: the atom's boolean result and the cursor
        position after the closing paren (for groups) or the atom
        token (for comparisons / key lookups).

    Raises:
        ValueError: On missing operand, unclosed parenthesis, or
            unexpected token.
    """
    if pos >= len(tokens):
        msg = "Missing operand"
        raise ValueError(msg)

    if tokens[pos] == "(":
        pos += 1  # consume '('
        value, pos = _parse_or(tokens, pos, context)
        if pos >= len(tokens) or tokens[pos] != ")":
            msg = "Unclosed parenthesis"
            raise ValueError(msg)
        return value, pos + 1  # consume ')'

    # Anything else is an atom (comparison or key lookup)
    token = tokens[pos]
    # Guard: bare operators that ended up as atoms are a parse error
    if token in ("AND", "OR", "NOT", ")"):
        msg = f"Unexpected token: {token}"
        raise ValueError(msg)
    return _eval_atom(token, context), pos + 1


# ── Compound detection heuristic ──────────────────────────────────

_COMPOUND_RE = re.compile(r"\b(?:AND|OR|NOT)\b|[()]", re.IGNORECASE)


_KEYWORDS = frozenset({"AND", "OR", "NOT"})


def _has_compound_operators(expression: str) -> bool:
    """Check if expression contains compound operators or parens.

    Returns:
        ``True`` when the expression contains ``AND`` / ``OR`` /
        ``NOT`` (word-bounded) or parentheses; ``False`` otherwise.
    """
    return _COMPOUND_RE.search(expression) is not None


# ── Public API ────────────────────────────────────────────────────


def evaluate_condition(
    expression: str,
    context: Mapping[str, object],
) -> bool:
    """Evaluate a condition expression against a context dict.

    This function aims to never raise -- all edge cases (empty
    expressions, missing keys, malformed comparisons) are caught
    and resolve to ``False``.  ``MemoryError`` and
    ``RecursionError`` propagate.  Parse errors are logged at
    WARNING level before returning ``False``.

    Supports compound expressions with AND, OR, NOT operators
    and parenthesized groups.  Operator precedence: NOT > AND > OR.

    Args:
        expression: The condition expression string.
        context: Runtime context values for evaluation.

    Returns:
        Boolean result of the expression evaluation.
    """
    expr = expression.strip()
    if not expr:
        return False

    try:
        return _evaluate_inner(expr, context)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            CONDITION_EVAL_PARSE_ERROR,
            expression=expr[:200],
        )
        return False


def _evaluate_inner(
    expr: str,
    context: Mapping[str, object],
) -> bool:
    """Core evaluation logic; may raise on malformed input.

    Returns:
        The boolean result of evaluating ``expr`` against ``context``;
        an over-complex or trailing-token expression resolves to
        ``False`` after logging a warning.
    """
    # Quick path: if no compound operators, use simple evaluation
    # for zero-overhead backward compatibility.  If the entire
    # expression is exactly a keyword (AND/OR/NOT), treat it as
    # a simple key lookup rather than routing to the compound parser.
    if not _has_compound_operators(expr) or expr.upper() in _KEYWORDS:
        return _eval_atom(expr, context)

    # Compound path: tokenize and parse.
    tokens = _tokenize(expr)
    if not tokens:
        return False
    if len(tokens) > _MAX_TOKEN_COUNT:
        logger.warning(
            CONDITION_EVAL_PARSE_ERROR,
            expression=expr[:200],
            reason="expression too complex",
            token_count=len(tokens),
        )
        return False
    result, pos = _parse_or(tokens, 0, context)
    if pos != len(tokens):
        logger.warning(
            CONDITION_EVAL_PARSE_ERROR,
            expression=expr[:200],
            reason="trailing tokens",
            consumed=pos,
            total=len(tokens),
        )
        return False
    return result

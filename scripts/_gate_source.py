#!/usr/bin/env python3
"""Shared fail-closed source-reading helpers for check_* gates.

A gate that silently skips a file it cannot read or parse fails OPEN: a
violation hiding in an unreadable or unparseable module slips through the
scan and the build stays green. These helpers raise ``GateSourceError``
instead, so the calling gate can fail the build (exit 2) rather than pass
a partial scan.

Usage in a gate::

    from _gate_source import GateSourceError, read_and_parse


    def main() -> int:
        try:
            findings = scan(...)  # calls read_and_parse internally
        except GateSourceError as exc:
            print(f"FAIL (scan could not read a file): {exc}", file=sys.stderr)
            return 2
        ...
"""

import ast
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Final

_BLOCK_FIELDS: Final[frozenset[str]] = frozenset(
    {"body", "orelse", "finalbody", "handlers"},
)


class GateSourceError(Exception):
    """A source file could not be read or parsed during a gate scan.

    Raised by the readers below so a gate fails closed (exit 2) instead of
    silently skipping the file and passing an incomplete scan.
    """


def read_source(path: Path) -> str:
    """Return the UTF-8 text of *path*.

    Args:
        path: File to read.

    Returns:
        The decoded file contents.

    Raises:
        GateSourceError: If the file cannot be read or decoded.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{path}: could not read source: {exc}"
        raise GateSourceError(msg) from exc


def read_and_parse(path: Path) -> tuple[str, ast.Module]:
    """Read *path* and parse it into a module AST.

    Args:
        path: Python source file to read and parse.

    Returns:
        A ``(text, tree)`` pair so callers needing line context avoid a
        second read.

    Raises:
        GateSourceError: If the file cannot be read, decoded, or parsed.
    """
    text = read_source(path)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{path}: could not parse source: {exc}"
        raise GateSourceError(msg) from exc
    return text, tree


def parse_source(path: Path) -> ast.Module:
    """Parse *path* into a module AST.

    Args:
        path: Python source file to parse.

    Returns:
        The parsed module AST.

    Raises:
        GateSourceError: If the file cannot be read, decoded, or parsed.
    """
    _, tree = read_and_parse(path)
    return tree


def direct_body_nodes(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[ast.AST]:
    """Yield descendants of *scope*'s executable body only.

    Traverses the statements in ``scope.body`` (so a function's own decorators,
    parameter defaults, and annotations are excluded) and stops at nested
    ``FunctionDef`` / ``AsyncFunctionDef`` / ``Lambda`` / ``ClassDef`` -- a node
    buried in a signature expression or an inner helper must not be attributed
    to the outer scope a gate is inspecting. Shared by the AST gates so their
    scope-boundary behaviour cannot drift apart.

    Args:
        scope: The module or function whose own body should be walked.

    Yields:
        Each descendant node belonging to *scope*'s own executable body.
    """
    stack: list[ast.AST] = []
    stack.extend(scope.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef
        ):
            continue
        stack.extend(ast.iter_child_nodes(node))


def reachable_statements(body: Sequence[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the statements of a block control flow can still reach.

    A statement following an unconditional ``return`` / ``raise`` /
    ``break`` / ``continue`` in the same block is dead, so an enforcement
    step parked there never runs. A gate that asserts "this check is
    present" must therefore ignore dead statements, or a single early
    ``return`` silently disables the contract while the gate stays green.

    Args:
        body: The statement block to traverse.

    Yields:
        Each reachable statement, recursing into nested blocks.
    """
    for stmt in body:
        yield stmt
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list):
                yield from reachable_statements(nested)
        for handler in getattr(stmt, "handlers", []):
            yield from reachable_statements(handler.body)
        if isinstance(stmt, ast.Return | ast.Raise | ast.Break | ast.Continue):
            return


def statement_expressions(stmt: ast.stmt) -> Iterator[ast.AST]:
    """Yield the expression nodes a statement evaluates directly.

    Nested blocks are skipped: :func:`reachable_statements` already walks
    those, and re-walking them here would resurrect dead code.

    Args:
        stmt: The statement whose own expressions should be walked.

    Yields:
        Every expression node belonging to the statement itself.
    """
    for name, value in ast.iter_fields(stmt):
        if name in _BLOCK_FIELDS:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, ast.AST):
                yield from ast.walk(item)


def reaching_alias_names(
    scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef,
    predicate: Callable[[ast.expr | None], bool],
) -> frozenset[str]:
    """Return names singly and unconditionally bound to a matching value.

    A conservative reaching-definition approximation for the AST gates: a name
    is trusted only when it is assigned exactly once in the scope (counting every
    store -- reassignments, loop / with / walrus targets included) AND that one
    assignment is a top-level statement of the scope body whose value satisfies
    *predicate*. A reassigned or conditionally-assigned name is rejected, so an
    alias is never trusted on a control-flow path where it may no longer hold the
    matching value (e.g. ``x = fence; x = raw; return x``).

    Args:
        scope: The module or function whose own body binds the aliases.
        predicate: Matches the value expression a trusted alias must be bound to.

    Returns:
        The trusted alias identifiers.
    """
    store_counts: dict[str, int] = {}
    for node in direct_body_nodes(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            store_counts[node.id] = store_counts.get(node.id, 0) + 1
    trusted: set[str] = set()
    for stmt in scope.body:
        if isinstance(stmt, ast.Assign) and predicate(stmt.value):
            trusted.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.value is not None
            and predicate(stmt.value)
        ):
            trusted.add(stmt.target.id)
    return frozenset(name for name in trusted if store_counts.get(name, 0) == 1)

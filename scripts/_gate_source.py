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
from pathlib import Path


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

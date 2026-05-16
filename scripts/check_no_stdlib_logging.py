#!/usr/bin/env python3
"""Pre-push / CI gate: no stdlib ``logging`` in application code.

Project convention (CLAUDE.md Logging): application code uses
``from synthorg.observability import get_logger``; it never imports the
stdlib ``logging`` module or calls ``logging.getLogger``. (``print`` is
already covered by ruff ``T20``; this gate covers the stdlib-logging
import path ruff does not.)

The observability package itself is the sanctioned wrapper around
stdlib logging / structlog, so ``src/synthorg/observability/`` is
allowlisted. Everything else under ``src/synthorg/`` is application
code and must route through ``get_logger``.

AST-based (string/comment mentions never false-positive). Flags:

* ``import logging`` / ``import logging as x``
* ``from logging import ...`` / ``from logging.config import ...``
* ``logging.getLogger(...)`` attribute access

Fail-closed on a syntax error. No baseline: the tree is expected
clean (the convention has been review-enforced); a genuine
pre-existing violation should be fixed, not frozen.

Usage::

    python scripts/check_no_stdlib_logging.py
    python scripts/check_no_stdlib_logging.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

SCAN_ROOT = Path("src/synthorg")
# The sanctioned stdlib-logging wrapper; legitimately imports logging.
ALLOWLIST_PREFIXES = ("src/synthorg/observability/",)


@dataclass(frozen=True)
class Violation:
    """One stdlib-logging use in application code."""

    file: str
    lineno: int
    detail: str


def _violations_in_tree(tree: ast.AST, rel: str) -> list[Violation]:
    out: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(
                Violation(rel, node.lineno, f"import {alias.name}")
                for alias in node.names
                if alias.name == "logging" or alias.name.startswith("logging.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "logging" or module.startswith("logging."):
                out.append(
                    Violation(rel, node.lineno, f"from {module} import ..."),
                )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "getLogger"
            and isinstance(node.value, ast.Name)
            and node.value.id == "logging"
        ):
            out.append(Violation(rel, node.lineno, "logging.getLogger(...)"))
    return out


def _scan_file(path: Path, repo_root: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        msg = f"{path}: {exc}"
        raise SyntaxError(msg) from exc
    return _violations_in_tree(tree, path.relative_to(repo_root).as_posix())


def _iter_py_files(repo_root: Path) -> Iterable[Path]:
    base = repo_root / SCAN_ROOT
    if not base.is_dir():
        return
    for py in sorted(base.rglob("*.py")):
        rel = py.relative_to(repo_root).as_posix()
        if any(rel.startswith(prefix) for prefix in ALLOWLIST_PREFIXES):
            continue
        yield py


def _run(repo_root: Path) -> int:
    violations: list[Violation] = []
    for py in _iter_py_files(repo_root):
        violations.extend(_scan_file(py, repo_root))
    if not violations:
        return 0
    print("Stdlib `logging` used in application code:")
    for v in violations:
        print(f"  {v.file}:{v.lineno} ({v.detail})")
    print(
        "\nFix: use `from synthorg.observability import get_logger` and "
        "`logger = get_logger(__name__)`. The stdlib-logging wrapper "
        "lives in src/synthorg/observability/ and is the only place "
        "allowed to import `logging`.",
    )
    return 1


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    return _run(args.repo_root.resolve())


if __name__ == "__main__":
    sys.exit(main())

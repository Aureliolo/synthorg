#!/usr/bin/env python3
"""Pre-commit gate: forbid bare ``Mock()`` / ``AsyncMock()`` / ``MagicMock()``.

Mocks that don't declare the interface they stand for can silently
absorb any attribute access. Production code can rename or drop a
method without a single test failing -- the mock just keeps returning
a child mock for the missing method. This is the "mock drift" finding
from issue #1604.

The gate matches a call when:

1. The callee is a ``Name`` or ``Attribute`` whose terminal
   identifier is one of ``Mock`` / ``AsyncMock`` / ``MagicMock``,
   covering both ``Mock()`` and ``mock.Mock()`` shapes.
2. The call has zero positional and zero keyword arguments. A
   ``spec=Class`` (or ``spec_set=Class``, or any positional first
   arg interpreted as the spec) is sufficient to declare the
   interface; only the bare-call form is forbidden.

Allowlist
---------

``scripts/mock_spec_baseline.txt`` is a frozen list of
``path:lineno:colno`` entries that the gate ignores. The baseline
captures all pre-existing bare-mock sites at the time the gate is
introduced so the gate can ship without forcing a 900-site cleanup
in the same PR. New sites cannot be added to the baseline silently:
``--update`` rewrites the baseline file, and the rewritten file
must be committed (and reviewed) for the new site to be allowed.

Usage
-----

    python scripts/check_mock_spec.py <file>...     # pre-commit
    python scripts/check_mock_spec.py --scan-all    # CI / tests
    python scripts/check_mock_spec.py --update      # regenerate baseline
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"
_BASELINE_PATH = _REPO_ROOT / "scripts" / "mock_spec_baseline.txt"

_MOCK_NAMES: frozenset[str] = frozenset({"Mock", "AsyncMock", "MagicMock"})
"""Class names whose bare-call form is forbidden."""

_BASELINE_HEADER = """\
# Frozen baseline of pre-existing bare Mock()/AsyncMock()/MagicMock()
# call sites in tests/. Each line is `path:lineno:colno` (POSIX path,
# 1-indexed line, 0-indexed column) sorted in deterministic order.
#
# scripts/check_mock_spec.py reads this file to suppress violations
# at these exact locations. New bare-mock sites NOT in this list will
# fail the pre-commit hook.
#
# Regenerate (rare; requires explicit user approval) with:
#   uv run python scripts/check_mock_spec.py --update
#
# Issue #1604 / W4 (test-quality audit).
"""


class _BareMockFinder(ast.NodeVisitor):
    """Locate bare ``<Mock|AsyncMock|MagicMock>()`` call sites.

    Matches both bare-name (``Mock()``) and attribute-form
    (``mock.Mock()``, ``unittest.mock.MagicMock()``) callees.

    Attributes:
        hits: Tuples of ``(lineno, col_offset)`` for each match.
    """

    def __init__(self) -> None:
        self.hits: list[tuple[int, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Match bare-call mocks (no positional args, no keyword args)."""
        if node.args or node.keywords:
            self.generic_visit(node)
            return
        terminal = _terminal_callee_name(node.func)
        if terminal is not None and terminal in _MOCK_NAMES:
            self.hits.append((node.lineno, node.col_offset))
        self.generic_visit(node)


def _terminal_callee_name(value: ast.expr) -> str | None:
    """Return the terminal identifier of a callee expression, or ``None``.

    Handles bare names (``Mock``), attribute chains (``mock.Mock``,
    ``unittest.mock.MagicMock``), and rejects anything else.
    """
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


class InspectionError(RuntimeError):
    """A source file could not be parsed for AST inspection."""


def _scan_file(path: Path) -> list[tuple[int, int]]:
    """Return the sorted list of ``(lineno, col_offset)`` hits in *path*."""
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        msg = f"failed to read {path}: {type(exc).__name__}: {exc}"
        raise InspectionError(msg) from exc
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        msg = f"failed to parse {path}: SyntaxError at line {exc.lineno}: {exc.msg}"
        raise InspectionError(msg) from exc
    finder = _BareMockFinder()
    finder.visit(tree)
    return sorted(finder.hits)


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _iter_test_files() -> Iterable[Path]:
    """Walk ``tests/`` for ``.py`` files (excluding the ``_shared`` package).

    ``tests/_shared/`` holds shared test utilities (``FakeClock``, etc.)
    that are imported by tests, not collected as tests themselves; they
    have no business being subject to the mock-spec gate.
    """
    shared_dir = _TESTS_ROOT / "_shared"
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if shared_dir in path.parents:
            continue
        yield path


def _load_baseline() -> set[str]:
    """Return the set of allowlisted ``path:lineno:colno`` entries."""
    if not _BASELINE_PATH.exists():
        return set()
    entries: set[str] = set()
    for line in _BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def _format_entry(rel_path: str, lineno: int, col: int) -> str:
    return f"{rel_path}:{lineno}:{col}"


def _scan(test_path: Path, baseline: set[str]) -> list[str]:
    """Return violation lines for *test_path* not present in *baseline*."""
    try:
        hits = _scan_file(test_path)
    except InspectionError as exc:
        return [f"{_rel(test_path)}: inspection failed: {exc}"]
    rel = _rel(test_path)
    violations: list[str] = []
    for lineno, col in hits:
        entry = _format_entry(rel, lineno, col)
        if entry in baseline:
            continue
        violations.append(
            f"{entry}: bare mock without spec= "
            f"(see scripts/mock_spec_baseline.txt for the allowlist)",
        )
    return violations


def _scan_all_for_baseline() -> list[str]:
    """Return every bare-mock site in ``tests/`` for baseline regeneration.

    Re-raises ``InspectionError`` instead of silently continuing on a
    parse failure: a baseline that quietly skips an unparseable file
    would let the gate suppress every bare mock in that file going
    forward, which is exactly the kind of silent failure the gate
    exists to prevent.
    """
    entries: list[str] = []
    for test_path in _iter_test_files():
        hits = _scan_file(test_path)
        rel = _rel(test_path)
        for lineno, col in hits:
            entries.append(_format_entry(rel, lineno, col))
    return sorted(entries)


def cmd_update() -> int:
    """Regenerate the baseline file from the current tree state."""
    entries = _scan_all_for_baseline()
    body = _BASELINE_HEADER + "\n".join(entries) + "\n"
    _BASELINE_PATH.write_text(body, encoding="utf-8")
    print(
        f"Wrote {len(entries)} entries to {_rel(_BASELINE_PATH)}.",
        file=sys.stderr,
    )
    return 0


def cmd_scan_all() -> int:
    """Scan every file in ``tests/`` (CI mode)."""
    baseline = _load_baseline()
    violations: list[str] = []
    for test_path in _iter_test_files():
        violations.extend(_scan(test_path, baseline))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point)."""
    baseline = _load_baseline()
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.is_relative_to(_TESTS_ROOT):
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan(path, baseline))
    return _report(violations)


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\nMock drift (#1604): bare Mock()/AsyncMock()/MagicMock() in tests/"
        " absorbs any attribute access. Production code can rename a method"
        " and no test fails.\n"
        "\nReplace with:"
        "\n    AsyncMock(spec=ConcreteClass)"
        "\n    MagicMock(spec=ConcreteClass)"
        "\n    Mock(spec=ConcreteClass)"
        "\n"
        "\nThe spec= argument restricts attribute access to the public"
        "\ninterface of ConcreteClass, so missing methods raise"
        "\nAttributeError instead of returning yet another mock."
        "\n"
        "\nIf the site MUST stay bare (rare; please justify in PR review),"
        "\nappend it to scripts/mock_spec_baseline.txt by running:"
        "\n    uv run python scripts/check_mock_spec.py --update",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Gate on bare Mock()/AsyncMock()/MagicMock() in tests/.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files to check (pre-commit supplies these).",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan the full tests/ tree (CI mode).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Regenerate scripts/mock_spec_baseline.txt from current state.",
    )
    args = parser.parse_args(argv)

    if args.update:
        return cmd_update()
    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())

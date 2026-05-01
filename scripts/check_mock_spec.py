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
2. The call does NOT declare a spec. A spec is declared via the
   first positional arg (``Mock(SomeClass)`` is an alias for
   ``Mock(spec=SomeClass)``) OR an explicit ``spec=`` / ``spec_set=``
   keyword arg. Other keyword args (``name=``, ``return_value=``,
   ``side_effect=``, ``wraps=``, ...) configure mock behaviour but
   do NOT declare the interface, so they don't exempt the call.
   Non-empty ``*args`` / ``**kwargs`` splats are conservatively
   skipped because a spec could be passed dynamically.

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
import re
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
        """Flag Mock-family calls that don't declare a spec.

        A spec is declared via:
          * the first positional arg (``Mock(SomeClass)`` is an alias
            for ``Mock(spec=SomeClass)``), OR
          * an explicit ``spec=`` / ``spec_set=`` keyword arg.

        Other keyword args (``name=``, ``return_value=``, ``side_effect=``,
        ``wraps=``, ...) configure mock behaviour but do NOT declare the
        interface, so they don't exempt the call from the gate. Empty
        splats (``*[]``, ``**{}``) also don't declare a spec.

        Non-empty ``*args`` / ``**kwargs`` splats are the one ambiguous
        case: a spec could be passed dynamically. The gate stays
        conservative and skips those (the bare-call form is what the
        rule targets; dynamic-splat call sites are vanishingly rare in
        the test suite and any false negative there is acceptable).
        """
        terminal = _terminal_callee_name(node.func)
        if terminal is None or terminal not in _MOCK_NAMES:
            self.generic_visit(node)
            return
        if _has_spec_positional(node.args) or _has_spec_keyword(node.keywords):
            self.generic_visit(node)
            return
        if _has_dynamic_splat(node.args, node.keywords):
            self.generic_visit(node)
            return
        self.hits.append((node.lineno, node.col_offset))
        self.generic_visit(node)


def _is_empty_splat(value: ast.expr) -> bool:
    """Return True if ``value`` is an empty tuple/list/dict literal.

    Used to recognise ``*()`` / ``*[]`` / ``**{}`` splats that pass
    no actual arguments at runtime.
    """
    if isinstance(value, (ast.Tuple, ast.List)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return False


def _is_literal_none(value: ast.expr) -> bool:
    """Return True if *value* is the literal ``None``.

    ``Mock(None)`` and ``Mock(spec=None)`` look like spec declarations
    syntactically but actually opt OUT of speccing -- ``unittest.mock``
    treats them as no spec at all. The gate must not let those slip
    through; recognising the literal here keeps the rule honest.
    """
    return isinstance(value, ast.Constant) and value.value is None


def _has_spec_positional(args: list[ast.expr]) -> bool:
    """Return True if the first positional arg declares a spec.

    ``Mock(SomeClass)`` is an alias for ``Mock(spec=SomeClass)``;
    the first positional arg counts as a spec declaration as long
    as it is a real value (not ``None`` and not an empty splat).
    """
    if not args:
        return False
    first = args[0]
    if isinstance(first, ast.Starred):
        return not _is_empty_splat(first.value)
    return not _is_literal_none(first)


def _has_spec_keyword(keywords: list[ast.keyword]) -> bool:
    """Return True if a non-None ``spec=`` / ``spec_set=`` is passed."""
    return any(
        kw.arg in ("spec", "spec_set") and not _is_literal_none(kw.value)
        for kw in keywords
    )


def _has_dynamic_splat(args: list[ast.expr], keywords: list[ast.keyword]) -> bool:
    """Return True if args/kwargs contain a non-empty splat.

    A non-empty splat (``*some_list``, ``**some_dict``) could pass
    a spec dynamically; the gate stays conservative and treats
    those as non-violations.
    """
    if any(
        isinstance(arg, ast.Starred) and not _is_empty_splat(arg.value) for arg in args
    ):
        return True
    return any(kw.arg is None and not _is_empty_splat(kw.value) for kw in keywords)


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


_BASELINE_ENTRY_PATTERN = re.compile(r"^.+:\d+:\d+$")


def _load_baseline() -> set[str]:
    """Return the set of allowlisted ``path:lineno:colno`` entries.

    Validates each non-empty, non-comment line against the
    ``path:lineno:colno`` shape and rejects duplicates. A corrupted
    baseline (typo, manual merge artifact, accidentally-edited
    binary) silently dropping entries would let real bare-mock
    sites slip past the gate; failing loud at load time is the only
    safe behaviour.
    """
    if not _BASELINE_PATH.exists():
        return set()
    entries: set[str] = set()
    errors: list[str] = []
    try:
        rel_path = _rel(_BASELINE_PATH)
    except ValueError:
        # Baseline path is outside the repo (test fixture or
        # custom relocation); fall back to the bare path so error
        # messages still cite something useful.
        rel_path = str(_BASELINE_PATH)
    for lineno, line in enumerate(
        _BASELINE_PATH.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _BASELINE_ENTRY_PATTERN.match(stripped):
            errors.append(
                f"{rel_path}:{lineno}: malformed entry (expected "
                f"'path:lineno:colno', got {stripped!r})",
            )
            continue
        if stripped in entries:
            errors.append(
                f"{rel_path}:{lineno}: duplicate entry {stripped!r}",
            )
            continue
        entries.add(stripped)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        msg = (
            f"{rel_path}: baseline failed validation "
            f"({len(errors)} error{'s' if len(errors) != 1 else ''}); "
            f"regenerate with 'uv run python scripts/check_mock_spec.py "
            f"--update' or fix by hand."
        )
        raise ValueError(msg)
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

    Sort key sorts by (path, lineno, col) numerically. Plain string
    sort would lexicographically order ``"...:1169:..."`` before
    ``"...:754:..."`` because ``'1' < '7'``, producing a baseline
    block where the same file's entries jump backwards mid-block.
    """
    entries: list[tuple[str, int, int]] = []
    for test_path in _iter_test_files():
        hits = _scan_file(test_path)
        rel = _rel(test_path)
        for lineno, col in hits:
            entries.append((rel, lineno, col))
    entries.sort()
    return [_format_entry(rel, lineno, col) for rel, lineno, col in entries]


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
    """Scan the given files (pre-commit entry point).

    Skips files under ``tests/_shared/`` for the same reason
    ``_iter_test_files`` does: the package holds shared test utilities
    (``FakeClock``, ...) that are imported by tests, not collected as
    tests themselves. Scanning them via the pre-commit path would let
    the gate disagree with the ``--scan-all`` / ``--update`` paths.
    """
    baseline = _load_baseline()
    shared_dir = _TESTS_ROOT / "_shared"
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.is_relative_to(_TESTS_ROOT):
            continue
        if shared_dir in path.parents:
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
        "\nMock drift: bare Mock()/AsyncMock()/MagicMock() in tests/"
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

#!/usr/bin/env python3
"""Pre-commit gate: forbid bare Mock at a typed boundary.

Catches bare ``Mock()`` / ``AsyncMock()`` / ``MagicMock()`` calls used
at a typed boundary. A typed boundary is a constructor / function
argument annotated with a
non-Mock type, an annotated local (``m: Service = Mock()``), or the
return / yield of a function whose return annotation is a concrete
type. Bare mocks at these sites silently absorb any attribute access:
production code can rename or drop a method without a single test
failing because the mock keeps returning a child mock for the missing
method. This is the "mock drift" finding from issue #1604.

The gate deliberately ignores the lower rungs of the test-double
ladder (``docs/reference/conventions.md`` section 12.1):

* mocks assigned to ``.return_value`` / ``.side_effect`` / ``wraps`` of
  another mock,
* mocks assigned via ``parent.attr = Mock()`` (attribute-bag
  reconfiguration of an already-specced mock),
* attribute-bag scratch objects (``m = Mock(); m.x = 1; m.y = 2`` used
  only locally inside a test),
* values inside ``dict`` / ``list`` / ``tuple`` literals,
* mocks bound to a name that is never passed across a typed boundary.

Detection runs in two passes. Pass 1 collects every bare-mock call.
Pass 2 walks parent pointers (``ast`` does not carry them, so the
gate builds the map up-front) and decides CATCH or SKIP per the rule
table in ``_decide_direct``. Only DIRECT typed-boundary substitutions
catch: ``Service(deps=Mock())`` (Mock is the call argument itself),
``m: T = Mock()`` (Mock is the RHS of an annotated assignment), and
``return Mock()`` from a function with a non-Mock return annotation.
Indirect substitutions (Mock bound to a name then passed to a typed
callable) are NOT caught -- precise detection there requires resolving
callee parameter annotations across modules, which is out of scope
for a pure-stdlib AST gate. The lower rungs of the test-double ladder
(``docs/reference/conventions.md`` section 12.1) cover that case as
documented discipline rather than gated enforcement.

Usage
-----

    python scripts/check_mock_spec.py <file>...     # pre-commit
    python scripts/check_mock_spec.py --scan-all    # CI / tests
"""

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_ROOT = _REPO_ROOT / "tests"

_MOCK_NAMES: frozenset[str] = frozenset(
    {
        "Mock",
        "AsyncMock",
        "MagicMock",
        "NonCallableMock",
        "NonCallableMagicMock",
        "PropertyMock",
    }
)
"""Class names whose bare-call form is the candidate set for Pass 1."""

_MOCK_FACTORY_NAMES: frozenset[str] = frozenset({"create_autospec", "mock_of"})
"""Factory names treated as Mock-class for SKIP purposes in Pass 2.

A ``create_autospec(T, ...)`` or ``mock_of[T](...)`` call is already
typed by construction, so a bare ``Mock()`` passed as a kwarg to it
(``return_value=Mock()``) configures the autospec rather than crossing
a typed boundary.
"""

_MOCK_TYPE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(?:Mock|AsyncMock|MagicMock|NonCallableMock|NonCallableMagicMock|"
    r"PropertyMock|Any|object)\b",
)
"""Words in an annotation textual form that signal "not a typed boundary"."""


# ---------------------------------------------------------------------
# Pass 1: candidate collection
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class _BareMockSite:
    """A bare ``Mock()`` call captured during Pass 1.

    Attributes:
        lineno: 1-indexed line of the call.
        col_offset: 0-indexed column of the call.
        node: The ``ast.Call`` node for the bare mock.
        enclosing_fn: The closest enclosing ``FunctionDef`` /
            ``AsyncFunctionDef``, or the ``Module`` if the site is at
            module / class scope. Drives both return-annotation lookup
            and the scope of name-usage tracking in Pass 2.5.
    """

    lineno: int
    col_offset: int
    node: ast.Call
    enclosing_fn: ast.FunctionDef | ast.AsyncFunctionDef | ast.Module


class _Collector(ast.NodeVisitor):
    """Walk a parsed module and record bare-mock candidate sites."""

    def __init__(self, tree: ast.Module) -> None:
        self._tree = tree
        self.candidates: list[_BareMockSite] = []
        self._fn_stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._fn_stack.append(node)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._fn_stack.append(node)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_bare_mock_call(node):
            enclosing: ast.FunctionDef | ast.AsyncFunctionDef | ast.Module
            enclosing = self._fn_stack[-1] if self._fn_stack else self._tree
            self.candidates.append(
                _BareMockSite(
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    node=node,
                    enclosing_fn=enclosing,
                ),
            )
        self.generic_visit(node)


def _is_bare_mock_call(node: ast.Call) -> bool:
    """Return True if *node* is a bare-mock candidate call.

    A call is a candidate when it targets a Mock-family class (by
    terminal name) and does not declare a spec via the first positional
    arg or a ``spec=`` / ``spec_set=`` keyword. Non-empty splats are
    conservatively skipped because a spec could be passed dynamically.
    """
    terminal = _terminal_callee_name(node.func)
    if terminal is None or terminal not in _MOCK_NAMES:
        return False
    if _has_spec_positional(node.args) or _has_spec_keyword(node.keywords):
        return False
    return not _has_dynamic_splat(node.args, node.keywords)


def _terminal_callee_name(value: ast.expr) -> str | None:
    """Terminal identifier of a callee expression, or None.

    Recurses into ``ast.Subscript`` so generic-subscript factory
    expressions (``mock_of[T]``) resolve to their base name.
    """
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Subscript):
        return _terminal_callee_name(value.value)
    return None


def _is_empty_splat(value: ast.expr) -> bool:
    """True if *value* is `()`, `[]`, or `{}` (an empty splat target)."""
    if isinstance(value, (ast.Tuple, ast.List)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return False


def _is_literal_none(value: ast.expr) -> bool:
    """True if *value* is the literal `None` constant."""
    return isinstance(value, ast.Constant) and value.value is None


def _has_spec_positional(args: list[ast.expr]) -> bool:
    """True if the first positional arg is a non-None spec target."""
    if not args:
        return False
    first = args[0]
    if isinstance(first, ast.Starred):
        return not _is_empty_splat(first.value)
    return not _is_literal_none(first)


def _has_spec_keyword(keywords: list[ast.keyword]) -> bool:
    """True if `spec=` or `spec_set=` is bound to a non-None value."""
    return any(
        kw.arg in ("spec", "spec_set") and not _is_literal_none(kw.value)
        for kw in keywords
    )


def _has_dynamic_splat(
    args: list[ast.expr],
    keywords: list[ast.keyword],
) -> bool:
    """True if call has a non-empty `*args` or `**kwargs` splat we cannot inspect."""
    if any(
        isinstance(arg, ast.Starred) and not _is_empty_splat(arg.value) for arg in args
    ):
        return True
    return any(kw.arg is None and not _is_empty_splat(kw.value) for kw in keywords)


# ---------------------------------------------------------------------
# Parent map (ast does not carry parent pointers)
# ---------------------------------------------------------------------


class _ParentMap:
    """Maps AST node identity to its immediate parent.

    Built once per module by walking ``ast.iter_child_nodes`` over
    every node. Pass 2 and Pass 2.5 query it instead of walking the
    tree repeatedly.
    """

    def __init__(self, tree: ast.AST) -> None:
        self._parents: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self._parents[id(child)] = parent

    def get(self, node: ast.AST) -> ast.AST | None:
        return self._parents.get(id(node))


# ---------------------------------------------------------------------
# Pass 2: direct-context decision
# ---------------------------------------------------------------------


class _Verdict(Enum):
    CATCH = auto()
    SKIP = auto()


def _decide_direct(  # noqa: C901, PLR0911, PLR0912 -- rule table reads top-down
    node: ast.AST,
    parents: _ParentMap,
) -> _Verdict:
    """Return the direct-context verdict for a bare mock call.

    Walks up through ``NamedExpr`` (walrus) so the outer context is
    used for the direct decision.
    """
    parent = parents.get(node)
    while isinstance(parent, ast.NamedExpr):
        node = parent
        parent = parents.get(node)

    if parent is None:
        return _Verdict.SKIP

    if isinstance(parent, ast.Call):
        return _verdict_for_call_arg(parent)

    if isinstance(parent, ast.keyword):
        grand = parents.get(parent)
        if isinstance(grand, ast.Call):
            return _verdict_for_call_arg(grand)
        return _Verdict.SKIP

    if isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return _Verdict.SKIP

    if isinstance(parent, ast.Assign):
        return _Verdict.SKIP

    if isinstance(parent, ast.AnnAssign):
        if not isinstance(parent.target, ast.Name):
            return _Verdict.SKIP
        if _is_mock_typed_annotation(parent.annotation):
            return _Verdict.SKIP
        return _Verdict.CATCH

    if isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom)):
        fn = _enclosing_fn(parent, parents)
        if fn is None:
            return _Verdict.SKIP
        if fn.returns is None:
            return _Verdict.SKIP
        if _is_mock_typed_annotation(fn.returns):
            return _Verdict.SKIP
        return _Verdict.CATCH

    if isinstance(parent, ast.Expr):
        return _Verdict.SKIP

    return _Verdict.SKIP


def _verdict_for_call_arg(call: ast.Call) -> _Verdict:
    """Return CATCH if *call*'s callee is non-Mock, else SKIP."""
    terminal = _terminal_callee_name(call.func)
    if terminal in _MOCK_NAMES or terminal in _MOCK_FACTORY_NAMES:
        return _Verdict.SKIP
    return _Verdict.CATCH


def _is_mock_typed_annotation(annotation: ast.expr) -> bool:
    """True if the annotation textual form contains a Mock / Any / object word."""
    return bool(_MOCK_TYPE_PATTERN.search(ast.unparse(annotation)))


def _enclosing_fn(
    node: ast.AST,
    parents: _ParentMap,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Walk up parents until a function-def or None."""
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


# ---------------------------------------------------------------------
# Combined decision
# ---------------------------------------------------------------------


def _decide_for_site(site: _BareMockSite, parents: _ParentMap) -> _Verdict:
    """Final CATCH / SKIP decision for *site*.

    The gate catches only the DIRECT typed-boundary substitutions
    (``Service(deps=Mock())``, ``m: T = Mock()``, ``return Mock()``
    from a typed fn). Indirect substitutions where a Mock is bound
    to a name and later passed to a typed callable are NOT caught
    here: identifying them precisely requires resolving callee
    parameter annotations, which is out of scope for a pure-stdlib
    AST gate. The lower-rung discipline (rungs 3 and below in
    ``docs/reference/conventions.md`` section 12.1) is documented
    rather than gated.
    """
    return _decide_direct(site.node, parents)


# ---------------------------------------------------------------------
# File scan / CLI
# ---------------------------------------------------------------------


class InspectionError(RuntimeError):
    """A source file could not be parsed for AST inspection."""


def _scan_file(path: Path) -> list[tuple[int, int]]:
    """Return the sorted list of (lineno, col) Pattern A hits in *path*."""
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

    parents = _ParentMap(tree)
    collector = _Collector(tree)
    collector.visit(tree)

    hits = [
        (site.lineno, site.col_offset)
        for site in collector.candidates
        if _decide_for_site(site, parents) is _Verdict.CATCH
    ]
    return sorted(hits)


def _rel(path: Path) -> str:
    """Repo-relative POSIX path for stable violation messages."""
    return path.resolve().relative_to(_REPO_ROOT).as_posix()


def _iter_test_files() -> Iterable[Path]:
    """Walk ``tests/`` for ``.py`` files (excluding ``_shared``).

    Both ``shared_dir`` and each yielded ``path`` are resolved so the
    parent-set comparison is robust to symlinks / bind-mounts between
    ``_TESTS_ROOT`` and the actual ``_shared`` directory.
    """
    shared_dir = (_TESTS_ROOT / "_shared").resolve()
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if shared_dir in path.resolve().parents:
            continue
        yield path


def _scan(test_path: Path) -> list[str]:
    """Return violation lines for *test_path*."""
    try:
        hits = _scan_file(test_path)
    except InspectionError as exc:
        return [f"{_rel(test_path)}: inspection failed: {exc}"]
    rel = _rel(test_path)
    return [
        f"{rel}:{lineno}:{col}: bare mock at typed boundary "
        f"(constructor / fn arg / annotated local / typed fixture return)"
        for lineno, col in hits
    ]


def cmd_scan_all() -> int:
    """Scan every file in ``tests/`` (CI mode)."""
    violations: list[str] = []
    for test_path in _iter_test_files():
        violations.extend(_scan(test_path))
    return _report(violations)


def cmd_scan_paths(paths: Iterable[str]) -> int:
    """Scan the given files (pre-commit entry point).

    Skips files under ``tests/_shared/`` for the same reason
    ``_iter_test_files`` does: the package holds shared test
    utilities that the gate's own helper tests live in, and is
    excluded by convention from the scan.
    """
    shared_dir = (_TESTS_ROOT / "_shared").resolve()
    tests_root = _TESTS_ROOT.resolve()
    violations: list[str] = []
    for p in paths:
        path = Path(p).resolve()
        if not path.is_relative_to(tests_root):
            continue
        if shared_dir in path.parents:
            continue
        if not path.exists() or path.suffix != ".py":
            continue
        violations.extend(_scan(path))
    return _report(violations)


def _report(violations: list[str]) -> int:
    """Print violations and return a pre-commit-friendly exit code."""
    if not violations:
        return 0
    for line in violations:
        print(line)
    print(
        "\nMock drift: a bare Mock()/AsyncMock()/MagicMock() at a typed"
        " boundary absorbs any attribute access. Production code can"
        " rename a method and no test fails."
        "\n"
        "\nFix by replacing with one of (see"
        " docs/reference/conventions.md section 12.1):"
        "\n    mock_of[ConcreteClass](method=...)"
        "\n    create_autospec(ConcreteClass, instance=True)"
        "\n    FakeClock()  # for the Clock seam"
        "\n    SimpleNamespace(...)  # attribute-bag scratch only",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Gate on bare Mock()/AsyncMock()/MagicMock() at typed boundaries in tests/."
        ),
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
    args = parser.parse_args(argv)

    if args.scan_all:
        return cmd_scan_all()
    return cmd_scan_paths(args.paths)


if __name__ == "__main__":
    sys.exit(main())

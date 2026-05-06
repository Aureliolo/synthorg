"""Pre-push / CI currency-aggregation-invariant gate.

Enforces the rule: every aggregation site over cost-bearing fields
(``cost``, ``amount``, ``total_cost``, ``usd``, ``eur``) must be
preceded by a same-currency guard call (``assert_currencies_match`` /
``assert_single_currency``) in the same enclosing scope, so
mixed-currency input raises
:class:`~synthorg.budget.errors.MixedCurrencyAggregationError` (HTTP
409) instead of producing a meaningless monetary total.

Detected aggregations:

* ``sum(<gen> for ... in ...)`` -- builtin
* ``math.fsum(...)``
* ``statistics.mean(...)`` / ``statistics.fmean(...)``
* ``fsum``, ``mean``, ``fmean`` when imported by name (e.g.
  ``from math import fsum``).

The first argument must be a generator / list / set comprehension whose
``elt`` is an attribute access ending in one of the guarded field names.

Per-line opt-out: append ``# lint-allow: currency-aggregation -- <reason>``
to the aggregation line.  The justification after ``--`` is required and
must be non-empty -- use it for legitimate single-currency contexts that
were already partitioned upstream.

Usage:
    uv run python scripts/check_currency_aggregation_invariant.py
    uv run python scripts/check_currency_aggregation_invariant.py --paths src/synthorg

Exit codes:
    0 -- no violations.
    1 -- one or more violations.
    2 -- configuration error (e.g. invalid ``--repo-root``).
"""

import argparse
import ast
import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Final

# ── Detection sets ──────────────────────────────────────────────

# Currency-bearing attribute names. The gate flags any aggregation
# whose generator/list/set-comprehension elt is ``<var>.<attr>`` with
# attr in this set.
_GUARDED_FIELDS: Final[frozenset[str]] = frozenset(
    {"cost", "amount", "total_cost", "usd", "eur"}
)

# Guard callable names. A textually preceding call to any of these in
# the same enclosing scope satisfies the invariant.  Both forms exist
# because the original private helper was record-shaped and the public
# replacement is codes-shaped; either signals the intent.
_GUARD_NAMES: Final[frozenset[str]] = frozenset(
    {"assert_currencies_match", "assert_single_currency"}
)

# ``Attribute`` chains we treat as target aggregators when the value
# part is a bare module name.  The (module, attr) tuples cover the
# qualified call form (``math.fsum``); bare-name forms are handled via
# ``_BARE_NAME_TARGETS``.
_QUALIFIED_TARGETS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("math", "fsum"),
        ("statistics", "mean"),
        ("statistics", "fmean"),
    }
)

# Bare names that always (``sum``) or possibly (``fsum`` / ``mean`` /
# ``fmean`` when imported) resolve to one of the four target functions.
# We treat all four as targets unconditionally: ``sum`` is always the
# builtin in practical Python code, and a project-defined function
# named ``fsum`` aggregating ``r.cost`` would be the same kind of
# violation.
_BARE_NAME_TARGETS: Final[frozenset[str]] = frozenset({"sum", "fsum", "mean", "fmean"})

_SUPPRESSION_MARKER: Final[str] = "lint-allow: currency-aggregation"

# ── Allowlist ───────────────────────────────────────────────────

# Files exempt from scanning.  The gate's own definition references
# field names in pattern strings; its tests feed synthetic fixtures
# whose whole purpose is to be flagged.  Keep narrow.
_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "scripts/check_currency_aggregation_invariant.py",
        "tests/unit/scripts/test_check_currency_aggregation_invariant.py",
    }
)


# ── Suppression marker ──────────────────────────────────────────


def _line_has_trailing_marker(line: str) -> bool:
    """Return ``True`` iff *line* carries the suppression marker.

    The marker must be followed by ``--`` and a non-empty justification
    (``# lint-allow: currency-aggregation -- partitioned upstream``).
    Tokenisation skips ``#`` characters embedded in string literals so
    the marker cannot be spoofed inside docstrings or string values.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError, IndentationError, SyntaxError:
        return False
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        comment = tok.string.lstrip("#").strip()
        if not comment.startswith(_SUPPRESSION_MARKER):
            continue
        suffix = comment[len(_SUPPRESSION_MARKER) :].strip()
        if suffix.startswith("--"):
            justification = suffix[2:].strip()
            if justification:
                return True
    return False


# ── AST analysis ────────────────────────────────────────────────


def _is_target_call(node: ast.Call) -> bool:
    """Return ``True`` if *node* is one of the watched aggregator calls."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in _BARE_NAME_TARGETS:
        return True
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and (func.value.id, func.attr) in _QUALIFIED_TARGETS
    )


def _comp_aggregates_currency_field(arg: ast.expr) -> bool:
    """``True`` when *arg* is a comprehension over a guarded attribute.

    Walks the comprehension element so wrapped/nested usages such as
    ``abs(r.cost)``, ``r.cost or 0.0``, or ``x.total_cost * scale``
    are still caught -- matching only bare attribute elements would let
    those bypass the gate even though they aggregate currency-bearing
    data the same way.
    """
    if not isinstance(arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return False
    return any(
        isinstance(sub, ast.Attribute) and sub.attr in _GUARDED_FIELDS
        for sub in ast.walk(arg.elt)
    )


def _is_guard_call(node: ast.Call) -> bool:
    """``True`` when *node* invokes one of the same-currency guards."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in _GUARD_NAMES:
        return True
    return isinstance(func, ast.Attribute) and func.attr in _GUARD_NAMES


def _comp_iter_source(arg: ast.expr) -> str | None:
    """Return a stable string identifier for a comprehension's iter source.

    Used to compare a guard's input collection against the aggregator's
    input collection: a guard like ``assert_currencies_match(x.currency
    for x in xs)`` only clears ``sum(y.cost for y in ys)`` when both
    iterate the same source (otherwise ``ys`` is effectively unguarded).

    Returns ``None`` for non-comprehension args, comprehensions with
    multiple ``for`` clauses, or iter expressions whose source cannot
    be canonicalised into a stable name (e.g. inline calls).  The
    caller treats ``None`` as "cannot prove a match" and falls back to
    the broader scope-only behaviour.
    """
    if not isinstance(arg, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return None
    if len(arg.generators) != 1:
        return None
    iter_node = arg.generators[0].iter
    return _expr_source_id(iter_node)


def _expr_source_id(node: ast.expr) -> str | None:  # noqa: PLR0911 -- four AST shapes
    """Canonicalise *node* to a stable dotted identifier or ``None``.

    Handles the common iter sources: ``records`` (``Name``),
    ``self.records`` / ``a.b.c`` (``Attribute`` chains), and
    subscripted access by literal index / string key
    (``records[0]``, ``buckets["a"]``).  Anything more dynamic
    (function calls, slices, comprehensions) yields ``None`` so the
    gate stays conservative -- the caller treats unknown sources as
    not-matchable rather than risk a silent false-negative.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _expr_source_id(node.value)
        if prefix is None:
            return None
        return f"{prefix}.{node.attr}"
    if isinstance(node, ast.Subscript):
        prefix = _expr_source_id(node.value)
        if prefix is None:
            return None
        slice_node = node.slice
        if isinstance(slice_node, ast.Constant):
            return f"{prefix}[{slice_node.value!r}]"
        return None
    return None


def _guard_call_iter_source(node: ast.Call) -> str | None:
    """Return the iter source of a guard call's first positional argument."""
    if not node.args:
        return None
    return _comp_iter_source(node.args[0])


def _enclosing_scope(node: ast.AST, parents: dict[int, ast.AST]) -> ast.AST | None:
    """Return the nearest enclosing FunctionDef / AsyncFunctionDef / Module."""
    current: ast.AST | None = parents.get(id(node))
    while current is not None:
        if isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module),
        ):
            return current
        current = parents.get(id(current))
    return None


_NESTED_SCOPES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
)


def _walk_current_scope(scope: ast.AST) -> list[ast.AST]:
    """Yield every node reachable inside *scope* without crossing nested scopes.

    ``ast.walk`` would descend into nested ``def`` / ``async def`` /
    ``class`` / ``lambda`` bodies; a guard call defined inside a nested
    helper does NOT actually run before the target call in the outer
    scope, so allowing it would create false negatives in the gate.
    """
    nodes: list[ast.AST] = []
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, _NESTED_SCOPES):
            continue
        nodes.append(child)
        nodes.extend(_walk_current_scope(child))
    return nodes


def _scope_has_preceding_guard(
    scope: ast.AST,
    target: ast.Call,
) -> bool:
    """Walk *scope* and return ``True`` if any guard call precedes *target*.

    "Preceding" means strictly earlier line, or same line with strictly
    earlier column.  The walk stops at nested scope boundaries so a
    guard inside an inner ``def`` / ``class`` / ``lambda`` cannot
    satisfy a target call in the enclosing scope.

    When the *target* iterates a determinable source (``sum(r.cost for
    r in records)``), a preceding guard only counts if its own input
    iterates the same source.  This prevents
    ``assert_currencies_match(x.currency for x in xs)`` from clearing a
    later ``sum(y.cost for y in ys)`` whose ``ys`` is effectively
    unguarded.  Targets whose source cannot be canonicalised
    (subscripted access through dynamic keys, inline call results,
    etc.) fall back to scope-only matching so the gate stays
    conservative without flooding callers with false positives at
    sites it cannot reason about.
    """
    target_pos = (target.lineno, target.col_offset)
    target_source: str | None = None
    if target.args:
        target_source = _comp_iter_source(target.args[0])
    for sub in _walk_current_scope(scope):
        if not isinstance(sub, ast.Call):
            continue
        if sub is target:
            continue
        if not _is_guard_call(sub):
            continue
        if (sub.lineno, sub.col_offset) >= target_pos:
            continue
        if target_source is None:
            return True
        guard_source = _guard_call_iter_source(sub)
        if guard_source is None or guard_source == target_source:
            return True
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Return ``{id(child): parent}`` for every node reachable from *tree*."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _is_call_suppressed(node: ast.Call, lines: list[str]) -> bool:
    """``True`` when *node*'s span carries a trailing suppression marker.

    Accepts the marker on the line preceding the call OR any line spanned
    by it (start through ``end_lineno``) so multi-line ``sum(...)``
    blocks can carry the marker on a line that fits within the
    88-character budget.
    """
    end_line = getattr(node, "end_lineno", None) or node.lineno
    for ln in range(node.lineno - 1, end_line + 1):
        if 0 < ln <= len(lines) and _line_has_trailing_marker(lines[ln - 1]):
            return True
    return False


_VIOLATION_DETAIL: Final[str] = (
    "aggregation over a currency-bearing attribute without a same-currency "
    "guard.  Call ``assert_currencies_match`` (from "
    "``synthorg.budget.currency``) on the input currencies before the "
    "aggregation, or add "
    "'# lint-allow: currency-aggregation -- <reason>' if the "
    "input is known to be single-currency by construction."
)


def _scan_file(file_path: Path, rel: str) -> list[str]:
    """Return violation messages for a single file."""
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{rel}:0: unable to scan file: {exc}"]
    try:
        tree = ast.parse(text, filename=str(file_path))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno or 0}: unable to parse file: {exc.msg}"]
    lines = text.splitlines()
    parents = _build_parent_map(tree)

    issues: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_target_call(node) or not node.args:
            continue
        if not _comp_aggregates_currency_field(node.args[0]):
            continue
        if _is_call_suppressed(node, lines):
            continue
        scope = _enclosing_scope(node, parents)
        if scope is not None and _scope_has_preceding_guard(scope, node):
            continue
        issues.append(f"{rel}:{node.lineno}: {_VIOLATION_DETAIL}")
    return issues


# ── Path resolution ─────────────────────────────────────────────


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments."""
    default_root = Path(__file__).resolve().parent.parent
    if repo_root is None:
        return default_root
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Resolve *root* to an absolute path strictly under *project_root*."""
    candidate = root if root.is_absolute() else project_root / root
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    project_root_str = os.fspath(project_root.resolve(strict=False))
    resolved_str = os.fspath(resolved)
    try:
        common = os.path.commonpath([project_root_str, resolved_str])
    except ValueError:
        return None
    if common != project_root_str:
        return None
    return resolved


def _git_tracked_python_files(
    abs_root: Path,
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` file under *abs_root* as ``(abs, rel)``.

    ``git ls-files dir/*.py`` only matches files in the immediate
    directory (pathspec globs do not recurse).  Pass the directory and
    filter ``.py`` extensions in Python so nested modules under
    ``src/synthorg/<subdomain>/`` are picked up.
    """
    rel_root = abs_root.relative_to(project_root).as_posix() or "."
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", rel_root],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    paths = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [(project_root / rel_path, rel_path) for rel_path in paths]


def _iter_targets(
    roots: list[Path],
    project_root: Path,
) -> list[tuple[Path, str]]:
    """Yield ``(absolute_path, posix_relative_path)`` for every file to scan."""
    targets: list[tuple[Path, str]] = []
    for root in roots:
        abs_root = _resolve_root(root, project_root)
        if abs_root is None or not abs_root.exists():
            continue
        for path, rel in _git_tracked_python_files(abs_root, project_root):
            if rel in _ALLOWLIST:
                continue
            targets.append((path, rel))
    return targets


def _scan_all(roots: list[Path], project_root: Path) -> int:
    """Run the scan, print issues, return total violation count."""
    total = 0
    for path, rel in _iter_targets(roots, project_root):
        for msg in _scan_file(path, rel):
            print(msg)
            total += 1
    return total


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["src/synthorg"],
        help="Roots to scan (relative to repo root).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help=(
            "Project root to anchor path resolution against.  Defaults "
            "to the ancestor directory of this script."
        ),
    )
    args = parser.parse_args()

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    roots = [Path(p) for p in args.paths]
    for root in roots:
        if _resolve_root(root, project_root) is None:
            print(
                f"refusing to scan path outside project root: {root}",
                file=sys.stderr,
            )
            return 2

    total = _scan_all(roots, project_root)
    if total:
        print(
            f"\n{total} currency-aggregation-invariant violation(s) found.  "
            "See docs/reference/regional-defaults.md for the same-currency "
            "contract and the opt-out marker format.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

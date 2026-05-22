"""Pre-push / CI gate enforcing the cost-recording chokepoint.

Every direct ``provider.complete(...)`` call site in
``src/synthorg/`` must either be on the explicit allowlist (the
chokepoint itself, the engine call path, infra probes) or open a
``cost_recording_scope`` in the same enclosing function so the
chokepoint inside ``BaseCompletionProvider.complete`` emits a
:class:`CostRecord` for the call.

Issue #1598 flagged 23 of 24 LLM completion paths bypassing cost
recording.  This gate locks the closed-form invariant in place so
future LLM call sites do not silently regress.

Usage:
    python scripts/check_provider_complete_chokepoint.py
    python scripts/check_provider_complete_chokepoint.py --paths src/synthorg
"""

import argparse
import ast
import sys
from pathlib import Path
from typing import Final, TypeGuard

# Files where direct ``provider.complete(...)`` is allowed without
# opening a ``cost_recording_scope``.  Use forward-slash paths
# relative to the repository root.
_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        # The chokepoint itself.
        "src/synthorg/providers/base.py",
        # Engine post-execution recorder consumes turns from
        # ExecutionResult, not the chokepoint.
        "src/synthorg/engine/loop_helpers.py",
        # Infrastructure probes -- not user-attributable cost.
        "src/synthorg/providers/management/service.py",
        "src/synthorg/providers/health_prober.py",
        # Docstring examples / non-runtime references.
        "src/synthorg/providers/registry.py",
        # Research pipeline LLM helper: invoked during an agent turn, so
        # it runs under the engine's ambient cost-recording scope (the
        # chokepoint records via the contextvar). The helper has no
        # agent/task context to open its own scope, and the
        # ResearchService additionally tracks per-run cost on the run.
        "src/synthorg/research/_llm.py",
    }
)


def _normalize_path(path: Path, repo_root: Path) -> str:
    return path.relative_to(repo_root).as_posix()


_PROVIDER_HINT_TOKENS: Final[frozenset[str]] = frozenset({"provider", "driver"})


def _receiver_looks_like_provider(receiver: ast.expr) -> bool:
    """Heuristic: does the call receiver look like a provider/driver?

    We accept identifiers that contain ``provider`` or ``driver``
    (case-insensitive) -- e.g. ``provider``, ``self._provider``,
    ``self.driver``, ``_PROVIDER`` -- which covers every cost-bearing
    call site in the codebase while excluding unrelated APIs that
    happen to expose a ``.complete()`` method (futures, tasks, etc.).
    """
    if isinstance(receiver, ast.Name):
        ident = receiver.id.lower()
    elif isinstance(receiver, ast.Attribute):
        ident = receiver.attr.lower()
    else:
        return False
    return any(token in ident for token in _PROVIDER_HINT_TOKENS)


def _is_complete_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """Return True if ``node`` is a ``<provider-ish>.complete(...)`` call."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "complete":
        return False
    return _receiver_looks_like_provider(func.value)


def _is_cost_recording_scope_with(node: ast.AST) -> bool:
    """Return True if ``node`` is a with/async-with on cost_recording_scope."""
    if not isinstance(node, ast.AsyncWith | ast.With):
        return False
    for item in node.items:
        ctx = item.context_expr
        if not isinstance(ctx, ast.Call):
            continue
        target = ctx.func
        if isinstance(target, ast.Name) and target.id == "cost_recording_scope":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "cost_recording_scope":
            return True
    return False


def _call_is_within_scope(
    parents: dict[ast.AST, ast.AST | None],
    node: ast.AST,
) -> bool:
    """Return True if ``node`` is lexically inside a ``cost_recording_scope``.

    Walks up ancestors and stops at the enclosing function (so a
    sibling scope earlier in the same function does not count).
    """
    cur = parents.get(node)
    while cur is not None:
        if _is_cost_recording_scope_with(cur):
            return True
        if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef):
            return False
        cur = parents.get(cur)
    return False


def _enclosing_func(
    parents: dict[ast.AST, ast.AST | None],
    node: ast.AST,
) -> ast.AsyncFunctionDef | ast.FunctionDef | None:
    """Return the nearest enclosing function/method for ``node``."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.AsyncFunctionDef | ast.FunctionDef):
            return cur
        cur = parents.get(cur)
    return None


def _check_file(path: Path, repo_root: Path) -> list[str]:
    """Return a list of violation messages for ``path``."""
    rel = _normalize_path(path, repo_root)
    if rel in _ALLOWLIST:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError, UnicodeDecodeError:
        return [f"{rel}: failed to parse"]

    parents: dict[ast.AST, ast.AST | None] = {tree: None}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    violations: list[str] = []
    for node in ast.walk(tree):
        if not _is_complete_call(node):
            continue
        func = _enclosing_func(parents, node)
        if func is None:
            # Module-level call -- almost certainly a docstring example
            # or stub; flag conservatively.
            violations.append(
                f"{rel}:{node.lineno}: direct .complete() at module level "
                f"-- add to allowlist if intentional"
            )
            continue
        if _call_is_within_scope(parents, node):
            continue
        violations.append(
            f"{rel}:{node.lineno}: direct .complete() not wrapped in "
            f"cost_recording_scope (function {func.name!r}). "
            f"Open the scope or extend _ALLOWLIST in this script."
        )
    return violations


def main() -> int:
    """Run the chokepoint gate; return non-zero on violations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="*",
        default=["src/synthorg"],
        help="Directories to scan (default: src/synthorg)",
    )
    args = parser.parse_args()
    repo_root = Path.cwd()
    violations: list[str] = []
    for spec in args.paths:
        root = repo_root / spec
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            violations.extend(_check_file(py_file, repo_root))
    if violations:
        sys.stderr.write(
            "Cost-recording chokepoint violations "
            "(see scripts/check_provider_complete_chokepoint.py):\n",
        )
        for v in violations:
            sys.stderr.write(f"  - {v}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

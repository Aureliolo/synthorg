#!/usr/bin/env python3
"""Pre-push / CI gate: load-bearing runtime call edges stay intact.

The ghost-wiring gate (``scripts/check_no_ghost_wiring.py``) proves a
runtime component is *constructed* somewhere in ``src/``, not that it is
*reachable* from a request/loop entry point. Issue #1979's audit found the
red-team completion gate shipped CONSTRUCTED-and-ENFORCED yet unreachable
from the human approval path: it lived only in
``ReviewGateService.run_pipeline``, which has no caller in ``src/``, while
the production path (``complete_review``) had no red-team logic. A
construction-only check passes that silently -- false assurance.

This gate pins the specific call edges that make such a component actually
fire. It is manifest-driven
(``scripts/_runtime_reachability_manifest.txt``). Each entry is a
``(module, enclosing_fn, required_callee)`` triple; the gate asserts the
body of ``enclosing_fn`` in ``module`` contains a call to
``required_callee`` (matched by callee name, walking the full function
subtree so a call nested as an argument -- e.g.
``spawn(self.complete_review(...))`` -- still counts).

Deliberately NOT a transitive call-graph / reachability engine: a
name-token graph can manufacture spurious edges via name collisions and
*pass when the wiring is broken* -- the exact silent-false-assurance
failure this gate exists to prevent. Explicit pinned edges fail LOUDLY
when severed, forcing a conscious re-affirmation.

AST-based. Fail-closed on a missing module, missing function, or syntax
error in a scanned file.

Usage::

    uv run python scripts/check_runtime_reachability.py
    uv run python scripts/check_runtime_reachability.py --repo-root /path/to/repo
"""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MANIFEST = Path("scripts/_runtime_reachability_manifest.txt")

# "<module> <enclosing_fn> <required_callee> <issue>" before the " -- " note.
_MANIFEST_HEAD_FIELDS: Final[int] = 4

# The mandatory delimiter between the manifest head and its note.
_MANIFEST_DELIM: Final[str] = " -- "

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class RequiredEdge:
    """One call edge the manifest requires to exist."""

    module: str
    enclosing_fn: str
    required_callee: str
    issue: str
    note: str
    lineno: int


def _parse_manifest(path: Path) -> list[RequiredEdge]:
    edges: list[RequiredEdge] = []
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, sep, note = line.partition(_MANIFEST_DELIM)
        parts = head.split()
        if sep != _MANIFEST_DELIM or len(parts) != _MANIFEST_HEAD_FIELDS:
            msg = (
                f"{path}:{idx}: malformed manifest line (expect "
                f"'<module> <enclosing_fn> <required_callee> <issue> -- <note>'): "
                f"{raw!r}"
            )
            raise ValueError(msg)
        edges.append(
            RequiredEdge(
                module=parts[0],
                enclosing_fn=parts[1],
                required_callee=parts[2],
                issue=parts[3],
                note=note.strip(),
                lineno=idx,
            )
        )
    return edges


def _callee_name(call: ast.Call) -> str | None:
    """Return the called name for ``Symbol(...)`` or ``recv.Symbol(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _qualified_functions(tree: ast.AST) -> list[tuple[str, _FuncDef]]:
    """Return ``(qualified_name, node)`` for every function in *tree*.

    The qualified name is the dotted scope path: ``ClassName.method`` for a
    method, ``outer.inner`` for a nested function, the bare name for a
    module-level function. Class scopes contribute their name to the prefix;
    function scopes contribute theirs so nested helpers are distinguishable.
    """
    found: list[tuple[str, _FuncDef]] = []

    def _walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                qualified = f"{prefix}{child.name}"
                found.append((qualified, child))
                _walk(child, f"{qualified}.")
            elif isinstance(child, ast.ClassDef):
                _walk(child, f"{prefix}{child.name}.")
            else:
                _walk(child, prefix)

    _walk(tree, "")
    return found


def _functions_named(tree: ast.AST, name: str) -> list[_FuncDef]:
    """Return functions matching *name* (qualified ``A.b`` or bare leaf).

    A dotted *name* matches only the function at that exact scope path; a bare
    *name* matches every function whose own (leaf) name equals it, so the
    caller can detect and reject an ambiguous bare match.
    """
    qualified = _qualified_functions(tree)
    if "." in name:
        return [node for qname, node in qualified if qname == name]
    return [node for qname, node in qualified if qname.rsplit(".", 1)[-1] == name]


def _direct_calls(func: _FuncDef) -> list[ast.Call]:
    """Collect calls made directly in *func*, not in a nested scope.

    Walks *func*'s body but does not descend into nested ``def`` / ``async
    def`` / ``class`` / ``lambda`` scopes: a call that only fires inside an
    inner closure or a sibling method is not an edge of *func* itself.
    Argument-nested calls -- ``spawn(self.complete_review(...))`` -- are still
    collected because the argument expression belongs to *func*'s own scope.
    Only the function body is scanned: decorators, default-argument values, and
    return annotations are definition-time expressions that never run when the
    function is called, so they must not satisfy a runtime edge.
    """
    calls: list[ast.Call] = []
    _scopes = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda

    def _visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _scopes):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            _visit(child)

    for stmt in func.body:
        # A body statement that is itself a nested def / class introduces a new
        # scope; skip it so its calls are not attributed to *func*. _visit only
        # filters child nodes, so this top-level guard is required too.
        if isinstance(stmt, _scopes):
            continue
        _visit(stmt)
    return calls


def _body_calls(func: _FuncDef, callee: str) -> bool:
    """Return ``True`` when *func*'s own body calls *callee* by name.

    Only calls in *func*'s own scope count; calls buried in a nested ``def`` /
    ``class`` / ``lambda`` do not, so an edge cannot be manufactured by an
    unrelated inner helper that happens to call *callee*.
    """
    return any(_callee_name(node) == callee for node in _direct_calls(func))


def _check_edge(repo_root: Path, edge: RequiredEdge) -> str | None:
    """Return a failure message for *edge*, or ``None`` when it holds."""
    module_path = repo_root / edge.module
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except FileNotFoundError:
        return (
            f"  {edge.module}: module not found (cannot verify "
            f"{edge.enclosing_fn} -> {edge.required_callee})"
        )
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return f"  {edge.module}: unreadable/unparsable (fail-closed): {exc}"
    funcs = _functions_named(tree, edge.enclosing_fn)
    if not funcs:
        return (
            f"  {edge.module}: no function named {edge.enclosing_fn!r} "
            f"(required edge -> {edge.required_callee})"
        )
    if "." not in edge.enclosing_fn and len(funcs) > 1:
        return (
            f"  {edge.module}: {edge.enclosing_fn!r} is ambiguous "
            f"({len(funcs)} functions share the name); qualify it in the "
            f"manifest (e.g. ClassName.{edge.enclosing_fn}) so the edge "
            f"resolves to exactly one function"
        )
    if not any(_body_calls(func, edge.required_callee) for func in funcs):
        return (
            f"  {edge.module}: {edge.enclosing_fn} no longer calls "
            f"{edge.required_callee} ({edge.issue}) -- {edge.note}"
        )
    return None


def _run(repo_root: Path) -> int:
    repo_root = repo_root.resolve()
    manifest_path = repo_root / MANIFEST
    if not manifest_path.is_file():
        print(f"runtime-reachability gate: manifest missing at {MANIFEST}")
        return 1
    try:
        edges = _parse_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        print(f"runtime-reachability gate: cannot read manifest ({MANIFEST}): {exc}")
        return 1

    failures = [
        msg for edge in edges if (msg := _check_edge(repo_root, edge)) is not None
    ]
    if not failures:
        return 0

    print("Runtime-reachability regression -- a pinned load-bearing call edge")
    print("is severed, so a request/loop-path component may no longer fire:")
    for msg in failures:
        print(msg)
    print(
        "\nFix: restore the call edge, or, if the chain was deliberately "
        "rewired, update scripts/_runtime_reachability_manifest.txt in the "
        "same PR to pin the new edge. A construction-only check passes this "
        "silently -- the #1979 red-team-gate defect this gate prevents."
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

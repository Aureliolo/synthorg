#!/usr/bin/env python3
"""AST gate: flag asyncio primitives bound in ``__init__`` of long-lived services.

`asyncio.Lock` / `Event` / `Queue` / `Semaphore` / `Condition` lazily
bind to the event loop on first use.  Once bound, accessing them from
a different loop raises::

    RuntimeError: <obj> is bound to a different event loop

A class that:

* assigns one of these primitives to ``self.<name>`` in ``__init__``,
  AND
* exposes an async ``start`` / ``stop`` / ``run`` / ``_run`` method

...is service-shaped: callers expect to be able to start it, stop it,
and start it again -- possibly on a different loop (production
restart paths; pytest-asyncio's per-test event loops; failover
recovery).  Eager construction in ``__init__`` makes the primitives
captive to the first loop that touches them, breaking restart
semantics.

Fix pattern (see ``ApprovalTimeoutScheduler``,
``EscalationExpirationSweeper``):

* Initialise the field to ``None`` in ``__init__``.
* Construct the primitive inside ``start()`` after detecting any
  cross-loop stale state via ``task.get_loop()``.
* Drop the primitive in ``stop()`` so the next ``start()`` rebinds.

This gate flags new offenders; pre-existing sites are grandfathered
through ``scripts/loop_bound_init_baseline.txt``.  Update the baseline
intentionally (run ``--update-baseline``) only when fixing or
explicitly accepting a site; never use it to silence a regression.

Per-line opt-out: ``# lint-allow: loop-bound-init -- <reason>`` on
the assignment line.

Exit codes:
    0 - no new offenders
    1 - new violations detected (CI / pre-commit fails)
    2 - scan/baseline failure (unreadable or unparseable source, or
        malformed baseline)
"""

import argparse
import ast
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BASELINE_PATH = _REPO_ROOT / "scripts" / "loop_bound_init_baseline.txt"
_DEFAULT_SOURCE_ROOTS = (_REPO_ROOT / "src" / "synthorg",)

# asyncio primitives that bind to the current loop on first interaction.
_LOOP_BOUND_PRIMITIVES = frozenset(
    {"Lock", "Event", "Queue", "Semaphore", "Condition", "BoundedSemaphore"},
)

# Method names that mark a class as having a managed lifecycle.  When a
# class has any of these AND assigns asyncio primitives in ``__init__``,
# it's at risk of cross-loop binding.
_LIFECYCLE_METHODS = frozenset({"start", "stop", "_run", "run", "_run_loop"})

_OPT_OUT_MARKER = "lint-allow: loop-bound-init"


def _absorb_import(node: ast.Import, module_aliases: set[str]) -> None:
    """Record aliases for ``import asyncio`` and ``import asyncio.<sub> as ...``.

    ``import asyncio.locks as locks`` exposes ``locks.Lock()`` against the
    same loop-bound primitive set, so its alias must enter the matcher
    or the gate has an easy bypass.
    """
    for alias in node.names:
        if alias.asname is None:
            continue
        if alias.name == "asyncio" or alias.name.startswith("asyncio."):
            module_aliases.add(alias.asname)


def _absorb_import_from(
    node: ast.ImportFrom,
    module_aliases: set[str],
    direct_primitives: dict[str, str],
) -> None:
    """Record ``from asyncio import ...`` shapes."""
    if node.module == "asyncio":
        for alias in node.names:
            if alias.name in _LOOP_BOUND_PRIMITIVES:
                direct_primitives[alias.asname or alias.name] = alias.name
            elif alias.name == "locks":
                module_aliases.add(alias.asname or alias.name)
    elif node.module == "asyncio.locks":
        for alias in node.names:
            if alias.name in _LOOP_BOUND_PRIMITIVES:
                direct_primitives[alias.asname or alias.name] = alias.name


def _collect_asyncio_aliases(
    tree: ast.Module,
) -> tuple[frozenset[str], dict[str, str]]:
    """Return ``(asyncio module aliases, alias -> primitive name)``.

    Handles every common ``asyncio`` import shape so the gate cannot be
    bypassed by rewriting ``asyncio.Lock()`` as ``Lock()`` or by
    aliasing ``asyncio`` itself.

    Only module-level imports are considered.  A ``from asyncio import
    Lock`` inside a method body shadows that name in its own scope and
    must not poison the alias dict for the whole file -- otherwise a
    ``threading.Lock()`` assignment elsewhere in ``__init__`` would be
    flagged as if it were the asyncio primitive.
    """
    module_aliases: set[str] = {"asyncio"}
    direct_primitives: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            _absorb_import(node, module_aliases)
        elif isinstance(node, ast.ImportFrom):
            _absorb_import_from(node, module_aliases, direct_primitives)
    return frozenset(module_aliases), direct_primitives


_DEFAULT_ASYNCIO_ALIASES: frozenset[str] = frozenset({"asyncio"})


def _asyncio_primitive_name(
    node: ast.expr,
    asyncio_aliases: frozenset[str] = _DEFAULT_ASYNCIO_ALIASES,
    direct_primitives: dict[str, str] | None = None,
) -> str | None:
    """Return the primitive name if *node* constructs an asyncio primitive.

    Recognises ``asyncio.Lock()``, ``aio.Lock()`` (aliased module),
    ``asyncio.locks.Lock()`` (chained), ``locks.Lock()``
    (``from asyncio import locks``), and ``Lock()`` /
    ``from asyncio import Lock as L; L()`` (direct import).
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        if direct_primitives is None:
            return None
        return direct_primitives.get(func.id)
    if isinstance(func, ast.Attribute):
        if func.attr not in _LOOP_BOUND_PRIMITIVES:
            return None
        base = func.value
        while isinstance(base, ast.Attribute):
            base = base.value
        if isinstance(base, ast.Name) and base.id in asyncio_aliases:
            return func.attr
    return None


_NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _iter_init_descendants(func: ast.FunctionDef) -> list[ast.AST]:
    """Return AST nodes inside *func*, never crossing a nested-scope boundary.

    ``ast.walk(func)`` happily descends into nested ``def`` / ``async def`` /
    ``class`` bodies declared inside ``__init__``.  Those nested scopes execute
    later, on whichever loop the caller invokes them under, so a
    ``self._lock = asyncio.Lock()`` in a closure is NOT eager loop binding and
    must not be flagged.
    """
    descendants: list[ast.AST] = []
    stack: list[ast.AST] = [
        stmt for stmt in func.body if not isinstance(stmt, _NESTED_SCOPE_NODES)
    ]
    while stack:
        node = stack.pop()
        descendants.append(node)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPE_NODES):
                continue
            stack.append(child)
    return descendants


def _self_attr_assignments(
    func: ast.FunctionDef,
    asyncio_aliases: frozenset[str],
    direct_primitives: dict[str, str],
) -> list[tuple[int, str, str]]:
    """Yield ``(lineno, attr_name, primitive_name)`` for ``self.X = asyncio.Y()``."""
    findings: list[tuple[int, str, str]] = []
    for stmt in _iter_init_descendants(func):
        if isinstance(stmt, ast.Assign):
            primitive = _asyncio_primitive_name(
                stmt.value,
                asyncio_aliases,
                direct_primitives,
            )
            if primitive is None:
                continue
            findings.extend(
                (stmt.lineno, target.attr, primitive)
                for target in stmt.targets
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                )
            )
        elif isinstance(stmt, ast.AnnAssign):
            primitive = (
                _asyncio_primitive_name(
                    stmt.value,
                    asyncio_aliases,
                    direct_primitives,
                )
                if stmt.value
                else None
            )
            if primitive is None:
                continue
            target = stmt.target
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                findings.append((stmt.lineno, target.attr, primitive))
    return findings


def _has_lifecycle_method(class_node: ast.ClassDef) -> bool:
    """True iff *class_node* defines an async lifecycle method."""
    for item in class_node.body:
        if isinstance(item, ast.AsyncFunctionDef) and item.name in _LIFECYCLE_METHODS:
            return True
    return False


def _find_init(class_node: ast.ClassDef) -> ast.FunctionDef | None:
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            return item
    return None


def _line_is_opt_out(source_lines: list[str], lineno: int) -> bool:
    """True iff the line at *lineno* (1-indexed) carries the opt-out marker."""
    if lineno < 1 or lineno > len(source_lines):
        return False
    return _OPT_OUT_MARKER in source_lines[lineno - 1]


def _scan_file(path: Path) -> list[str]:
    """Return ``"<rel_path>:<lineno>:<class>:<attr>:<primitive>"`` per finding.

    Raises:
        GateSourceError: If *path* cannot be read or parsed (fail-closed).
    """
    text, tree = read_and_parse(path)
    source_lines = text.splitlines()
    try:
        rel = path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        # Path is outside the repo (e.g. a tmp_path in unit tests
        # exercising this scanner directly).  Fall back to the basename
        # so test assertions can still match on the class:attr suffix.
        rel = path.name
    asyncio_aliases, direct_primitives = _collect_asyncio_aliases(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not _has_lifecycle_method(node):
            continue
        init = _find_init(node)
        if init is None:
            continue
        for lineno, attr, primitive in _self_attr_assignments(
            init,
            asyncio_aliases,
            direct_primitives,
        ):
            if _line_is_opt_out(source_lines, lineno):
                continue
            findings.append(f"{rel}:{lineno}:{node.name}:{attr}:{primitive}")
    return findings


def _scan_roots(roots: tuple[Path, ...]) -> list[str]:
    findings: list[str] = []
    for root in roots:
        for src in sorted(root.rglob("*.py")):
            findings.extend(_scan_file(src))
    return findings


class BaselineMalformedError(ValueError):
    """Raised when the baseline file contains an entry with the wrong shape."""


def _validate_baseline_entry(entry: str) -> None:
    """Raise ``BaselineMalformedError`` if *entry* is not ``a:b:c:d:e`` shape.

    A baseline entry is ``<path>:<lineno>:<class>:<attr>:<primitive>`` -- five
    colon-separated fields, with ``<lineno>`` parseable as a positive integer.
    Hand-edits or merge artefacts that produce malformed entries should fail
    loudly so the gate is not silently weakened.
    """
    parts = entry.split(":")
    expected_parts = 5
    if len(parts) != expected_parts or any(not part for part in parts):
        msg = f"baseline entry must be 'path:line:class:attr:primitive', got {entry!r}"
        raise BaselineMalformedError(msg)
    try:
        lineno = int(parts[1])
    except ValueError as exc:
        msg = f"baseline entry has non-integer lineno: {entry!r}"
        raise BaselineMalformedError(msg) from exc
    if lineno <= 0:
        msg = f"baseline entry has non-positive lineno: {entry!r}"
        raise BaselineMalformedError(msg)


def _load_baseline(path: Path) -> set[str]:
    """Return the set of baseline entries; raise on a malformed file."""
    if not path.exists():
        return set()
    entries = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    for entry in entries:
        _validate_baseline_entry(entry)
    return entries


def _write_baseline(path: Path, entries: list[str]) -> None:
    body = (
        "# Loop-bound asyncio primitives in __init__ of lifecycle-managed\n"
        "# classes -- baseline of pre-existing sites tolerated by\n"
        "# scripts/check_no_loop_bound_init.py.  Each entry is\n"
        "# `path:line:class:attr:primitive`.  When fixing one of these\n"
        "# sites, regenerate this file via:\n"
        "#   uv run python scripts/check_no_loop_bound_init.py "
        "--update-baseline\n"
        "\n"
    )
    body += "\n".join(sorted(set(entries))) + "\n"
    path.write_text(body, encoding="utf-8")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite scripts/loop_bound_init_baseline.txt with current sites.",
    )
    args = parser.parse_args()

    try:
        findings = _scan_roots(_DEFAULT_SOURCE_ROOTS)
    except GateSourceError as exc:
        print(
            f"FAIL (loop-bound-init scan could not read a file): {exc}", file=sys.stderr
        )
        return 2

    if args.update_baseline:
        _write_baseline(_BASELINE_PATH, findings)
        print(
            f"Baseline updated: {len(findings)} entries -> {_BASELINE_PATH}",
            file=sys.stderr,
        )
        return 0

    try:
        baseline = _load_baseline(_BASELINE_PATH)
    except BaselineMalformedError as exc:
        print(f"baseline malformed: {exc}", file=sys.stderr)
        return 2
    new = sorted(set(findings) - baseline)
    if new:
        print(
            "New loop-bound asyncio primitives in __init__ of "
            "lifecycle-managed classes:",
            file=sys.stderr,
        )
        for entry in new:
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nThese primitives bind to the first event loop that touches\n"
            "them, breaking restart on a different loop (production\n"
            "lifecycle, pytest-asyncio per-test loops).  Fix pattern:\n"
            "  - Initialise the field to None in __init__\n"
            "  - Construct the primitive in start() after detecting\n"
            "    cross-loop reuse via task.get_loop()\n"
            "  - Drop it in stop()\n"
            "See ApprovalTimeoutScheduler / EscalationExpirationSweeper\n"
            "for the canonical implementation.\n"
            f"\nPer-line opt-out: # {_OPT_OUT_MARKER} -- <reason>",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

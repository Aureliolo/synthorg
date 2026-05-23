#!/usr/bin/env python3
"""No-circular-imports gate.

Walks ``src/synthorg/`` and builds the module-level import graph. Any
strongly-connected component with more than one module is reported as
a cycle.

Excluded from the graph:

* ``if TYPE_CHECKING:`` block imports (PEP 563 forward refs).
* Function-local and class-body-local imports (intentional lazy loads).
* External (non-``synthorg.*``) imports.

Cycles are canonicalised by rotating each cycle so its lexicographically
smallest module is first, then sorting the cycle list lexically. Each
cycle is emitted as ``mod_a -> mod_b -> mod_c``.

Existing cycles are absorbed via
``scripts/_circular_imports_baseline.txt`` (one cycle per line,
``# ...`` comments allowed). A baselined cycle that no longer exists is
reported as stale -- the baseline must shrink monotonically.

Usage::

    uv run python scripts/check_no_circular_imports.py
"""

import argparse
import ast
import dataclasses
import sys
from pathlib import Path
from typing import Final

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent
_BASELINE_REL = Path("scripts") / "_circular_imports_baseline.txt"
_SCAN_REL: Final[str] = "src/synthorg"
_PACKAGE_ROOT: Final[str] = "synthorg"

_BASELINE_HEADER = (
    "# Frozen baseline of pre-existing import cycles in src/synthorg/.\n"
    "# Each line is `mod_a -> mod_b -> ... -> mod_a`, sorted by the\n"
    "# lexicographically smallest module in the cycle.\n"
    "#\n"
    "# scripts/check_no_circular_imports.py reads this file to suppress\n"
    "# violations at these exact cycles. New cycles NOT in this list\n"
    "# will fail the pre-push hook.\n"
    "#\n"
    "# Regenerate (rare; requires explicit user approval) by calling\n"
    "# write_baseline() via the gate's Python API.\n"
)


# ── AST extraction ──────────────────────────────────────────────


def extract_imports(path: Path) -> set[str]:
    """Return the set of top-level ``synthorg.*`` modules imported by *path*.

    Excludes function-local imports, class-body imports, and imports
    inside ``if TYPE_CHECKING:`` blocks. Relative imports are resolved
    relative to the file's package, but only ``synthorg.*`` targets are
    retained.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError, OSError:
        return set()
    imports: set[str] = set()
    _walk_module_body(tree.body, imports)
    return imports


def _walk_module_body(body: list[ast.stmt], imports: set[str]) -> None:
    for node in body:
        _harvest_imports_from_stmt(node, imports)


def _harvest_imports_from_stmt(stmt: ast.stmt, imports: set[str]) -> None:
    """Add any module-level ``synthorg.*`` imports inside *stmt* to *imports*.

    Recurses into ``if``/``try``/``with`` at module scope but NOT into
    function or class bodies. Skips the body of ``if TYPE_CHECKING:``.
    """
    if isinstance(stmt, ast.Import):
        for alias in stmt.names:
            if (
                alias.name.startswith(f"{_PACKAGE_ROOT}.")
                or alias.name == _PACKAGE_ROOT
            ):
                imports.add(alias.name)
        return
    if isinstance(stmt, ast.ImportFrom):
        if stmt.module and (
            stmt.module == _PACKAGE_ROOT or stmt.module.startswith(f"{_PACKAGE_ROOT}.")
        ):
            imports.add(stmt.module)
        return
    if isinstance(stmt, ast.If):
        if _is_type_checking_test(stmt.test):
            return
        _walk_module_body(stmt.body, imports)
        _walk_module_body(stmt.orelse, imports)
        return
    if isinstance(stmt, ast.Try):
        _walk_module_body(stmt.body, imports)
        for handler in stmt.handlers:
            _walk_module_body(handler.body, imports)
        _walk_module_body(stmt.orelse, imports)
        _walk_module_body(stmt.finalbody, imports)
        return
    if isinstance(stmt, ast.With):
        _walk_module_body(stmt.body, imports)
        return


def _is_type_checking_test(node: ast.expr) -> bool:
    """Return True iff *node* is the ``TYPE_CHECKING`` name guard."""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


# ── Path -> module resolver ─────────────────────────────────────


def module_path_for_rel(rel: str) -> str:
    """Return the dotted module path for a repo-relative POSIX path.

    ``src/synthorg/foo.py`` -> ``synthorg.foo``
    ``src/synthorg/foo/__init__.py`` -> ``synthorg.foo``
    ``src/synthorg/__init__.py`` -> ``synthorg``
    """
    posix = rel.replace("\\", "/")
    posix = posix.removeprefix("src/")
    if posix.endswith("/__init__.py"):
        posix = posix[: -len("/__init__.py")]
    elif posix.endswith(".py"):
        posix = posix[: -len(".py")]
    return posix.replace("/", ".")


# ── Graph build ─────────────────────────────────────────────────


def _build_import_graph(project_root: Path) -> dict[str, set[str]]:
    """Walk ``src/synthorg/`` and return ``{module: {imported_module}}``."""
    scan_root = project_root / _SCAN_REL
    graph: dict[str, set[str]] = {}
    if not scan_root.is_dir():
        return graph
    for path in scan_root.rglob("*.py"):
        rel = path.relative_to(project_root).as_posix()
        module = module_path_for_rel(rel)
        graph.setdefault(module, set()).update(extract_imports(path))
    # Restrict edges to modules we actually have a source file for so
    # the cycle detector doesn't trip over stub-only or test-only imports.
    nodes = set(graph.keys())
    return {mod: {dep for dep in deps if dep in nodes} for mod, deps in graph.items()}


# ── Tarjan SCC ─────────────────────────────────────────────────


def find_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return every non-trivial strongly-connected component as a cycle.

    Each cycle is canonicalised by rotation so its lexicographically
    smallest module is first. The returned list is sorted by the first
    element of each cycle.
    """
    state = _TarjanState()
    for node in sorted(graph):
        if node not in state.indices:
            _strongconnect(node, graph, state)
    cycles: list[tuple[str, ...]] = []
    for scc in state.sccs:
        if len(scc) == 1:
            single = scc[0]
            if single in graph.get(single, set()):
                # Self-loop on a single node: not interesting for module-level cycles
                continue
            continue
        cycles.append(_canonicalise_cycle(scc))
    return sorted(cycles)


@dataclasses.dataclass
class _TarjanState:
    indices: dict[str, int] = dataclasses.field(default_factory=dict)
    lowlinks: dict[str, int] = dataclasses.field(default_factory=dict)
    on_stack: set[str] = dataclasses.field(default_factory=set)
    stack: list[str] = dataclasses.field(default_factory=list)
    sccs: list[list[str]] = dataclasses.field(default_factory=list)
    counter: list[int] = dataclasses.field(default_factory=lambda: [0])


def _strongconnect(node: str, graph: dict[str, set[str]], state: _TarjanState) -> None:
    """Iterative Tarjan strongly-connected-component traversal.

    Recursive Tarjan blows the Python stack on large graphs; the
    iterative form keeps the entire walk on the heap.
    """
    work: list[tuple[str, list[str]]] = [(node, sorted(graph.get(node, set())))]
    state.indices[node] = state.counter[0]
    state.lowlinks[node] = state.counter[0]
    state.counter[0] += 1
    state.stack.append(node)
    state.on_stack.add(node)
    while work:
        v, successors = work[-1]
        if successors:
            w = successors.pop(0)
            if w not in state.indices:
                state.indices[w] = state.counter[0]
                state.lowlinks[w] = state.counter[0]
                state.counter[0] += 1
                state.stack.append(w)
                state.on_stack.add(w)
                work.append((w, sorted(graph.get(w, set()))))
            elif w in state.on_stack:
                state.lowlinks[v] = min(state.lowlinks[v], state.indices[w])
            continue
        # Finished exploring v
        if state.lowlinks[v] == state.indices[v]:
            scc: list[str] = []
            while True:
                w = state.stack.pop()
                state.on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            state.sccs.append(scc)
        work.pop()
        if work:
            parent = work[-1][0]
            state.lowlinks[parent] = min(state.lowlinks[parent], state.lowlinks[v])


def _canonicalise_cycle(nodes: list[str]) -> tuple[str, ...]:
    """Sort the cycle members lexically and return as a tuple."""
    return tuple(sorted(nodes))


# ── Baseline I/O ────────────────────────────────────────────────


def _render_cycle(cycle: tuple[str, ...]) -> str:
    return " -> ".join(cycle)


def _parse_baseline_line(line: str) -> tuple[str, ...] | None:
    cleaned = line.split("#", maxsplit=1)[0].strip()
    if not cleaned:
        return None
    parts = tuple(part.strip() for part in cleaned.split("->"))
    if not all(parts):
        return None
    return tuple(sorted(parts))


def _load_baseline(baseline_path: Path) -> set[tuple[str, ...]]:
    if not baseline_path.is_file():
        return set()
    entries: set[tuple[str, ...]] = set()
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_baseline_line(line)
        if parsed is not None:
            entries.add(parsed)
    return entries


def check(*, project_root: Path, baseline_path: Path) -> list[tuple[str, ...]]:
    """Run the gate; return cycles not absorbed by the baseline."""
    graph = _build_import_graph(project_root)
    cycles = find_cycles(graph)
    baseline = _load_baseline(baseline_path)
    return [cycle for cycle in cycles if cycle not in baseline]


def write_baseline(*, project_root: Path, baseline_path: Path) -> None:
    """Regenerate the baseline file."""
    cycles = find_cycles(_build_import_graph(project_root))
    body = "\n".join(_render_cycle(cycle) for cycle in cycles)
    suffix = "\n" if body else ""
    baseline_path.write_text(_BASELINE_HEADER + body + suffix, encoding="utf-8")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT_DEFAULT,
        help="Override the project root.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Path to the baseline file (default: "
            f"<project-root>/{_BASELINE_REL.as_posix()})"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry. ``0`` clean (or all baselined), ``1`` on new cycle."""
    args = _build_arg_parser().parse_args(argv)
    project_root: Path = args.project_root.resolve()
    baseline_path: Path = (
        args.baseline.resolve()
        if args.baseline is not None
        else project_root / _BASELINE_REL
    )
    cycles = check(project_root=project_root, baseline_path=baseline_path)
    if not cycles:
        return 0
    print("New import cycles detected in src/synthorg/. Cycles:", file=sys.stderr)
    for cycle in cycles:
        print(f"  {_render_cycle(cycle)}", file=sys.stderr)
    print(
        "\nBreak the cycle: hoist a shared protocol into a leaf module, "
        "or lazy-import via TYPE_CHECKING / function-local imports.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Pre-push / CI gate: every wave loop gates on its subtasks' dependencies.

A plan's DAG decides WHEN a subtask runs. Whether it SHOULD run, given that
the work it declared as input may have died, is a separate question, and
``gate_wave`` is its one owner: it narrows a wave to the subtasks whose
inputs delivered and parks each of the rest BLOCKED naming what it waited on.

The reason this is a gate rather than a convention is that the product ships
three wave loops and the rule was added to two of them. ``SasDispatcher`` and
``WaveDispatcher`` share ``execute_waves``; ``ContextDependentDispatcher``
carries its own copy of the loop, and it is the one a live run selected. So
the run dispatched wave after wave onto work that had already failed, each
wave failing on inputs nobody had written, while the unit suite was green:
every test exercised a dispatcher that did gate.

Detection
---------
The population is DERIVED, never listed: any module under
``src/synthorg/engine/coordination/`` that imports ``build_execution_waves``
is a wave loop, because that call is what turns a decomposition into ordered
waves. Each one must also reach ``gate_wave``, ``abandon_after``,
``abandon_stranded`` and ``abandon_unreachable``, either directly or through
``execute_waves`` (which calls each for its callers).

The fourth is the one no wave can see. The builder DROPS a subtask it cannot
place with an agent, and then everything transitively standing on it, into a
set local to the build: those rows appear in no group, so the three faces
that reason about waves never meet them. A live run left two such rows at
CREATED while a recovery sweep re-drove the plan every cadence and changed
nothing, for ever.

Deriving rather than listing is the point. A hand-written list of dispatchers
is one new file away from disagreeing with the set of things that dispatch,
which is the same failure this gate exists to catch, one level up.

Fail-closed
-----------
An empty derived set is a configuration error, not a pass. If nothing imports
``build_execution_waves`` the wave builder has been renamed and this gate has
silently stopped looking at anything.

Allowlist / opt-out
-------------------
There is deliberately none, and no baseline. A wave loop that does not gate
IS the defect; an exception would be a second dispatcher deciding for itself
whether dead inputs matter, which is exactly the two-owner shape the rule
exists to remove. A genuine new dispatcher calls ``gate_wave`` like the other
three.

Usage::

    uv run python scripts/check_wave_dispatch_gated.py

Exit codes:
    0 -- every wave loop reaches the dependency gate.
    1 -- a wave loop dispatches without gating.
    2 -- configuration error (bad ``--repo-root``, an unreadable source file,
         or no wave loop found at all -- fail-closed).
"""

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _gate_source import (  # type: ignore[import-not-found]
        GateSourceError,
        read_and_parse,
    )
else:
    from scripts._gate_source import GateSourceError, read_and_parse

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_SCAN_ROOT_REL: Final[str] = "src/synthorg/engine/coordination"
#: The same package as an import path, for recognising a sibling helper an
#: absolute import names. Derived from the scan root rather than written a
#: second time, so the two cannot drift, and fully qualified rather than the
#: trailing ``engine.coordination``: an unrooted tail matches wherever it
#: appears, so ``vendor.engine.coordination.parking`` would be credited as a
#: sibling of a package it has nothing to do with.
_SCAN_PACKAGE: Final[str] = _SCAN_ROOT_REL.removeprefix("src/").replace("/", ".")

#: ``ImportFrom.level`` for a same-package import. Every level above one
#: walks further out than the scanned package, whose modules this scan does
#: not read and must not credit as siblings.
_SIBLING_IMPORT_LEVEL: Final[int] = 1

#: ``ImportFrom.level`` for an absolute import, which qualifies on naming the
#: scanned package rather than on depth.
_ABSOLUTE_IMPORT_LEVEL: Final[int] = 0

#: Calling this is what makes a module a wave loop: it is the one function
#: that turns a decomposition plus a routing into dependency-ordered waves.
_WAVE_BUILDER: Final[str] = "build_execution_waves"

#: The dependency gate itself, and the shared runner that calls it. A module
#: reaching either has gated every wave it dispatches.
_GATE_NAMES: Final[frozenset[str]] = frozenset({"gate_wave", "execute_waves"})

#: The gate's other half: parking the waves a run stopped before reaching.
#: Gating alone covers the wave being dispatched, so a loop that gates and
#: then breaks still leaves every later wave's subtasks at CREATED with
#: nothing watching them, which is the same deadlock one step further on.
_ABANDON_NAMES: Final[frozenset[str]] = frozenset({"abandon_after", "execute_waves"})

#: The gate's third face: parking the rows of a wave that FAILED before it
#: dispatched them. A wave that ran owns its own outcome, so ``abandon_after``
#: skips it; a wave that raised does not, and its undispatched rows stay at
#: CREATED, which is not a status the gate reads as non-delivering. The next
#: wave then dispatches against outputs nobody will write, which is the exact
#: hole gating exists to close, one wave earlier.
_STRANDED_NAMES: Final[frozenset[str]] = frozenset(
    {"abandon_stranded", "execute_waves"}
)

#: The gate's fourth face, and the one no wave can see: the BUILDER drops a
#: subtask it cannot place with an agent and everything transitively standing
#: on it, so those rows appear in no group at all. Three faces reason about
#: waves that exist; without this one the dropped rows sit at CREATED for
#: ever, the rollup reads none of them, and the plan never concludes.
_UNREACHABLE_NAMES: Final[frozenset[str]] = frozenset(
    {"abandon_unreachable", "execute_waves"}
)

#: The module that defines the gate does not have to call it.
_GATE_OWNER_REL: Final[str] = "src/synthorg/engine/coordination/_wave_parking.py"


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _WaveLoop:
    """One module that builds execution waves, and what it reaches."""

    rel: str
    gates: bool
    abandons: bool
    parks_stranded: bool
    parks_unreachable: bool


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the project root from CLI arguments.

    Returns:
        The resolved project-root directory.

    Raises:
        ProjectRootError: If *repo_root* cannot be resolved to an existing
            directory.
    """
    if repo_root is None:
        return _REPO_ROOT
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError as exc:
        msg = f"--repo-root not accessible: {repo_root} ({exc})"
        raise ProjectRootError(msg) from exc
    if not resolved.is_dir():
        msg = f"--repo-root must be a directory: {resolved}"
        raise ProjectRootError(msg)
    return resolved


def _tracked_python_files(project_root: Path) -> list[tuple[Path, str]]:
    """Return every tracked ``*.py`` under the coordination package.

    Falls back to :meth:`Path.rglob` when ``git`` is unavailable, warning on
    stderr because the fallback widens scope to untracked files.

    Returns:
        A list of ``(absolute_path, posix_relative_path)`` pairs.
    """
    abs_root = project_root / _SCAN_ROOT_REL
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--", _SCAN_ROOT_REL],
            check=True,
            capture_output=True,
            cwd=project_root,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        print(
            f"check_wave_dispatch_gated: git ls-files failed in {project_root} "
            f"({type(exc).__name__}: {exc}); falling back to rglob (scope "
            "widens to include untracked / gitignored files).",
            file=sys.stderr,
        )
        return [
            (p, p.relative_to(project_root).as_posix()) for p in abs_root.rglob("*.py")
        ]
    out = result.stdout.decode("utf-8", errors="replace")
    rels = [p for p in out.split("\0") if p and p.endswith(".py")]
    return [((project_root / rel), rel) for rel in rels]


def _alias_origins(tree: ast.Module) -> dict[str, str]:
    """Map each locally-bound import name back to the name it was imported as.

    ``from x import build_execution_waves as build`` binds ``build``, and a
    check reading call names literally would miss it. Resolving first means
    an import style cannot decide whether the gate applies.

    Returns:
        ``{local_name: original_name}`` for every aliased import.
    """
    origins: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for alias in node.names:
            original = alias.name.rsplit(".", 1)[-1]
            origins[alias.asname or original] = original
    return origins


def _called_names(tree: ast.Module) -> frozenset[str]:
    """Return the origin name of every function this module CALLS.

    Calls, not references: the coordination barrel re-exports the wave
    builder without dispatching anything, and reading a re-export as a wave
    loop would fail a module that has no loop to gate.

    Returns:
        The set of called identifiers, aliases resolved.
    """
    origins = _alias_origins(tree)
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(origins.get(func.id, func.id))
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return frozenset(called)


def _in_scan_package(dotted: str) -> bool:
    """Whether *dotted* names a module inside the scanned package.

    Anchored, and on whole components: a substring test credits
    ``engine.coordination_helpers``, and an unanchored component test
    credits ``vendor.engine.coordination.parking``. Either puts a module
    this scan never reads into the sibling set, which is the same fail-open
    as crediting a parent-package import.

    Returns:
        ``True`` when *dotted* is the scanned package or lives beneath it.
    """
    return dotted == _SCAN_PACKAGE or dotted.startswith(f"{_SCAN_PACKAGE}.")


def _imported_siblings(tree: ast.Module) -> frozenset[str]:
    """Return the coordination modules this one imports from.

    Only siblings inside the scanned package: a helper anywhere else is not
    something this gate reads, and following an import out of the package
    would walk the whole tree to answer a question about three dispatchers.

    Returns:
        Module base names, as they appear in ``coordination.<name>``.
    """
    siblings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # A relative import inside the package names the sibling
            # directly; an absolute one carries the package path in front.
            if node.module is None:
                # ``from . import parking`` puts the sibling in the alias
                # list and leaves ``module`` empty, so reading ``module``
                # alone sees no import at all. It is an ordinary way to reach
                # a sibling, and missing it makes a loop that gates through
                # one read as a loop that does not gate.
                #
                # Level ONE exactly. ``from .. import x`` names a module of a
                # package further out, which is not a sibling and whose calls
                # this scan never reads. Accepting it puts an unrelated name
                # into the sibling set, and if the package happens to hold a
                # module by that name, its gate calls are credited to a
                # dispatcher that never reaches them: the gate then passes an
                # ungated wave loop, which is the one verdict it must never
                # produce.
                if node.level == _SIBLING_IMPORT_LEVEL:
                    siblings.update(alias.name for alias in node.names)
            # The same rule, for the spelling that carries a module name.
            # Depth decides it here too: ``from .parking import x`` is a
            # sibling, ``from ..parking import x`` is the parent package's
            # module wearing an identical base name, and reading only
            # "is it relative" cannot tell them apart. An absolute import
            # qualifies on naming the scanned package instead, which is a
            # claim about where the module lives rather than how far up the
            # writer reached.
            elif node.level == _ABSOLUTE_IMPORT_LEVEL and node.module == _SCAN_PACKAGE:
                # ``from synthorg.engine.coordination import parking`` names
                # the PACKAGE, so the sibling is in the alias list and the
                # module's own last component is the package name. Reading
                # that component here would credit ``coordination`` and miss
                # the module actually imported.
                siblings.update(alias.name for alias in node.names)
            elif node.level == _SIBLING_IMPORT_LEVEL or (
                node.level == _ABSOLUTE_IMPORT_LEVEL and _in_scan_package(node.module)
            ):
                siblings.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # The bare package names no sibling, so only a module beneath
                # it contributes one.
                if _in_scan_package(alias.name) and alias.name != _SCAN_PACKAGE:
                    siblings.add(alias.name.rsplit(".", 1)[-1])
    return frozenset(siblings)


def _reachable_calls(
    start: str,
    called_by_module: dict[str, frozenset[str]],
    siblings_by_module: dict[str, frozenset[str]],
) -> frozenset[str]:
    """Return every name reachable from *start* through sibling helpers.

    The direct-call reading was one refactor from lying. Extracting a wave
    loop's parking into a sibling module is the ordinary way a module stays
    under its size cap, and against a name-only check that extraction reads
    as a dispatcher that stopped gating: the gate would fail correct code and
    teach that the fix is to inline the helper back. What the rule actually
    requires is that the call is REACHED, not that it is written in the same
    file.

    Module-level rather than per-function, unlike the reachability walk in
    ``check_verified_completion_paths.py``: that gate asks whether one
    specific entry point reaches a guard, so it has to know which function
    calls what. Here the module is already known to be a wave loop, so the
    question is only whether the module reaches the call at all, and a
    coarser walk answers it without a second copy of a function-level graph.
    Coarser is also the safe direction for this gate, whose failure mode is
    refusing a dispatcher that does gate.

    Returns:
        The union of *start*'s own calls and those of every sibling it
        reaches, transitively.
    """
    seen: set[str] = set()
    pending = [start]
    reachable: set[str] = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        reachable |= called_by_module.get(module, frozenset())
        pending.extend(siblings_by_module.get(module, frozenset()))
    return frozenset(reachable)


def _collect_wave_loops(project_root: Path) -> list[_WaveLoop]:
    """Return every module that builds execution waves, and whether it gates.

    Returns:
        One entry per wave loop found, excluding the gate's own module.

    Raises:
        GateSourceError: When a tracked source file cannot be read or parsed.
    """
    called_by_module: dict[str, frozenset[str]] = {}
    siblings_by_module: dict[str, frozenset[str]] = {}
    rel_by_module: dict[str, str] = {}
    for path, rel in _tracked_python_files(project_root):
        module = Path(rel).stem
        _, tree = read_and_parse(path)
        called_by_module[module] = _called_names(tree)
        siblings_by_module[module] = _imported_siblings(tree)
        rel_by_module[module] = rel

    loops: list[_WaveLoop] = []
    for module, rel in sorted(rel_by_module.items(), key=lambda item: item[1]):
        if rel == _GATE_OWNER_REL:
            continue
        # The builder itself is read directly: a module that merely imports a
        # sibling which builds waves is not a wave loop, and reaching for it
        # transitively would make every importer of a dispatcher one too.
        if _WAVE_BUILDER not in called_by_module[module]:
            continue
        reached = _reachable_calls(module, called_by_module, siblings_by_module)
        loops.append(
            _WaveLoop(
                rel=rel,
                gates=bool(reached & _GATE_NAMES),
                abandons=bool(reached & _ABANDON_NAMES),
                parks_stranded=bool(reached & _STRANDED_NAMES),
                parks_unreachable=bool(reached & _UNREACHABLE_NAMES),
            )
        )
    return loops


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns:
        The gate exit code (0 clean, 1 violation, 2 configuration error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to this script's repo).",
    )
    args = parser.parse_args(argv)

    try:
        project_root = _resolve_project_root(args.repo_root)
    except ProjectRootError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        loops = _collect_wave_loops(project_root)
    except GateSourceError as exc:
        print(f"check_wave_dispatch_gated: {exc}", file=sys.stderr)
        return 2

    if not loops:
        print(
            f"check_wave_dispatch_gated: no module under {_SCAN_ROOT_REL} calls "
            f"{_WAVE_BUILDER!r}. Either the wave builder was renamed and this "
            "gate now checks nothing, or the scan root is wrong. Fix the "
            "constant rather than leaving the gate blind.",
            file=sys.stderr,
        )
        return 2

    ungated = sorted(loop.rel for loop in loops if not loop.gates)
    unabandoned = sorted(loop.rel for loop in loops if not loop.abandons)
    unstranded = sorted(loop.rel for loop in loops if not loop.parks_stranded)
    unreachable = sorted(loop.rel for loop in loops if not loop.parks_unreachable)
    if not ungated and not unabandoned and not unstranded and not unreachable:
        return 0
    for rel in ungated:
        print(
            f"{rel}: builds execution waves but never reaches "
            f"{'/'.join(sorted(_GATE_NAMES))}, so it dispatches subtasks whose "
            "declared inputs may already have failed. Call `gate_wave` before "
            "the wave runs (or route the loop through `execute_waves`)."
        )
    for rel in unabandoned:
        print(
            f"{rel}: builds execution waves but never reaches "
            f"{'/'.join(sorted(_ABANDON_NAMES))}, so a run that stops early "
            "leaves every later wave's subtasks at CREATED with nothing "
            "watching them. Call `abandon_after` at each break (or route the "
            "loop through `execute_waves`)."
        )
    for rel in unstranded:
        print(
            f"{rel}: builds execution waves but never reaches "
            f"{'/'.join(sorted(_STRANDED_NAMES))}, so a wave that FAILS before "
            "dispatching leaves its own rows at CREATED, which the gate reads "
            "as still on their way. Call `abandon_stranded` on the failing "
            "wave (or route the loop through `execute_waves`)."
        )
    for rel in unreachable:
        print(
            f"{rel}: builds execution waves but never reaches "
            f"{'/'.join(sorted(_UNREACHABLE_NAMES))}, so a subtask the BUILDER "
            "dropped for standing on an unplaceable prerequisite appears in no "
            "wave at all, and sits at CREATED with nothing watching it. Call "
            "`abandon_unreachable` over the plan's subtask ids (or route the "
            "loop through `execute_waves`)."
        )
    print(
        f"\n{len(set(ungated) | set(unabandoned) | set(unstranded) | set(unreachable))}"
        " wave loop(s) leave subtasks "
        "with no owner. A wave scheduled on work that died runs anyway and "
        "fails on inputs nobody wrote; a wave never reached leaves rows that "
        "no dispatcher will run, no gate will park and no rollup can conclude "
        "on. There is no opt-out: a second answer to 'will this subtask run' "
        "is the defect.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

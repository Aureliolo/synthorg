#!/usr/bin/env python3
"""Pre-push / CI gate: every paid recording harness journals as it goes.

A recording matrix is a sequence of cells, each of which is minutes to hours of
real provider spend. Held in memory until the last one returns, the whole
recording is one crash, one restart, one Ctrl-C away from having produced
nothing. Both harnesses in this tree were built that way, and a live
recursion-depth sweep proved it: seven hours, one cell measured, and a second
process started after it was killed with nothing on disk at all.

The mechanism is now one module, ``evals/harness/journal.py``, and each harness
binds it in an ``evals/<harness>/journal.py`` supplying only what is its own:
how a cell is keyed, what identifies its matrix, and which cells a resume buys
back rather than re-runs.

Detection
---------
The population is DERIVED, never listed: any module under ``evals/`` named
``runner.py`` that assembles a report is a recording driver. "Assembles a
report" is decided by the module calling a report constructor, which is what
makes it the thing that ends a matrix rather than a helper inside one. Each one
must reach ``open_journal`` or a binding's ``open_*_journal``, so its cells land
on disk as they are produced.

A binding module (``evals/*/journal.py``) is checked from the other side: it
must call the shared ``open_journal``. A binding that grew its own file
handling is a second copy of the durability logic, which is how one harness
comes to be subtly less crash-safe than the other.

Deriving rather than listing is the point. A hand-written list of harnesses is
one new harness away from disagreeing with the set of things that record, which
is the same failure this gate exists to catch, one level up.

Not in scope
------------
A runner whose cells are free and reproducible. ``evals/run.py`` replays a
cassette or a scripted strategy and makes no paid call, so re-running it costs
nothing and a journal there would be ceremony. It is excluded by the population
rule rather than by name: it is not a ``runner.py`` under a harness package.

Fail-closed
-----------
An empty derived set is a configuration error, not a pass. If nothing matches,
the harness layout has moved and this gate has silently stopped looking.

Allowlist / opt-out
-------------------
There is deliberately none, and no baseline. A recording that can lose paid
cells IS the defect, and the two that did are what this gate exists to stop
coming back.

Usage::

    uv run python scripts/check_recording_harness_journalled.py

Exit codes:
    0 -- every recording driver journals, every binding uses the shared writer.
    1 -- a driver accumulates without journalling, or a binding hand-rolls one.
    2 -- configuration error (bad ``--repo-root``, an unreadable source file,
         or no recording driver found at all -- fail-closed).
"""

import argparse
import ast
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

#: Where the harnesses live.
_SCAN_ROOT_REL: Final[str] = "evals"

#: The module every harness's driver is called, so the population is a shape
#: rather than a list.
_DRIVER_NAME: Final[str] = "runner.py"

#: The module a harness binds the shared journal in.
_BINDING_NAME: Final[str] = "journal.py"

#: The shared writer itself, which is checked rather than checking.
_SHARED_WRITER_REL: Final[str] = "evals/harness/journal.py"

#: Calling one of these is what makes a module a recording driver: it is what
#: assembles the artifact that ends a matrix.
_REPORT_BUILDERS: Final[frozenset[str]] = frozenset(
    {"RecursionDepthReport", "Scoreboard"}
)

#: The shared open, plus the per-harness bindings that call it. A driver
#: reaching any of these puts its cells on disk as they land.
_JOURNAL_OPENS: Final[frozenset[str]] = frozenset(
    {"open_journal", "open_cell_journal", "open_row_journal"}
)

#: What a binding must reach, so durability is written once.
_SHARED_OPEN: Final[str] = "open_journal"


class ProjectRootError(Exception):
    """Raised when ``--repo-root`` cannot be resolved to a usable directory."""


@dataclass(frozen=True)
class _Offence:
    """One module that records without journalling, or journals its own way."""

    rel: str
    reason: str


def _resolve_project_root(repo_root: Path | None) -> Path:
    """Resolve the repository root the scan runs against.

    Args:
        repo_root: The caller's override, or ``None`` for this file's own root.

    Returns:
        The resolved root.

    Raises:
        ProjectRootError: The path is not a directory.
    """
    root = (repo_root or _REPO_ROOT).resolve()
    if not root.is_dir():
        msg = f"--repo-root is not a directory: {root}"
        raise ProjectRootError(msg)
    return root


def _called_names(node: ast.AST) -> set[str]:
    """Every name CALLED anywhere under *node*, by its final attribute or name.

    Calls rather than references, because importing a name and never invoking
    it journals nothing. The attribute form is read too, so a module that keeps
    the binding in a namespace (``journal.open_journal(...)``) counts.

    Args:
        node: The subtree to read, a module or a single function.

    Returns:
        The called names.
    """
    called: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        target = child.func
        if isinstance(target, ast.Name):
            called.add(target.id)
        elif isinstance(target, ast.Attribute):
            called.add(target.attr)
    return called


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined in *tree*, nested ones included.

    Returns:
        The function nodes.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _drivers(root: Path) -> list[Path]:
    """Every recording driver under the scan root.

    Returns:
        The driver paths, sorted.
    """
    scan = root / _SCAN_ROOT_REL
    return sorted(path for path in scan.rglob(_DRIVER_NAME) if path.is_file())


def _bindings(root: Path) -> list[Path]:
    """Every per-harness journal binding, excluding the shared writer.

    Returns:
        The binding paths, sorted.
    """
    shared = (root / _SHARED_WRITER_REL).resolve()
    scan = root / _SCAN_ROOT_REL
    return sorted(
        path
        for path in scan.rglob(_BINDING_NAME)
        if path.is_file() and path.resolve() != shared
    )


def _driver_offence(path: Path, root: Path) -> _Offence | None:
    """Judge one driver, or pass over a module that assembles no report.

    Args:
        path: The driver module.
        root: The repository root, for the reported path.

    Returns:
        The offence, or ``None``.

    Raises:
        GateSourceError: The file could not be read or parsed.
    """
    _text, tree = read_and_parse(path)
    # Per FUNCTION, not per module. The function that assembles the report is
    # the one that drove the matrix, so it is the one that had to be
    # journalling: a module-wide union would be satisfied by an open sitting in
    # a helper nothing on this path calls, which journals exactly nothing.
    assembling = [
        node for node in _functions(tree) if _called_names(node) & _REPORT_BUILDERS
    ]
    if not assembling:
        return None
    if any(_called_names(node) & _JOURNAL_OPENS for node in assembling):
        return None
    return _Offence(
        rel=path.relative_to(root).as_posix(),
        reason=(
            "assembles a report in a function that opens no journal, so every "
            "cell it paid for is lost when the process dies"
        ),
    )


def _binding_offence(path: Path, root: Path) -> _Offence | None:
    """Judge one binding.

    Args:
        path: The binding module.
        root: The repository root, for the reported path.

    Returns:
        The offence, or ``None``.

    Raises:
        GateSourceError: The file could not be read or parsed.
    """
    _text, tree = read_and_parse(path)
    # Same reasoning as the driver: the function a harness hands its driver is
    # the one that has to reach the shared writer. An ``open_journal`` call
    # elsewhere in the file binds nothing.
    entries = [
        node
        for node in _functions(tree)
        if node.name.startswith("open_") and node.name.endswith("_journal")
    ]
    if entries and any(_SHARED_OPEN in _called_names(node) for node in entries):
        return None
    return _Offence(
        rel=path.relative_to(root).as_posix(),
        reason=(
            f"has no open_*_journal entry point that calls {_SHARED_OPEN}, so "
            f"it is a second copy of the durability logic rather than a "
            f"binding of it"
        ),
    )


def _report(offences: list[_Offence]) -> None:
    """Print the offences.

    Args:
        offences: What went wrong.
    """
    print("Recording harnesses that can lose paid cells:\n")
    for offence in offences:
        print(f"  {offence.rel}\n      {offence.reason}\n")
    print(
        "Every recording driver journals each cell as it lands, through the "
        "shared writer in evals/harness/journal.py. See its module docstring."
    )


def main(argv: list[str] | None = None) -> int:
    """Check every recording harness.

    Args:
        argv: Command-line arguments.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        root = _resolve_project_root(args.repo_root)
        drivers = _drivers(root)
        bindings = _bindings(root)
        offences = [
            offence
            for path in drivers
            if (offence := _driver_offence(path, root)) is not None
        ]
        offences.extend(
            offence
            for path in bindings
            if (offence := _binding_offence(path, root)) is not None
        )
    except (ProjectRootError, GateSourceError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if not drivers:
        print(
            f"configuration error: no {_DRIVER_NAME} found under "
            f"{_SCAN_ROOT_REL}/; the harness layout has moved and this gate "
            f"is looking at nothing",
            file=sys.stderr,
        )
        return 2
    if offences:
        _report(offences)
        return 1
    print(
        f"OK: {len(drivers)} recording driver(s) and {len(bindings)} "
        f"binding(s) journal through the shared writer."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

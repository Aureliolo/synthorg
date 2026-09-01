# module-kind: code
"""Do the units of one cell agree on the names they share?

The question the contract stage exists to answer, asked of the trees rather
than of anybody's account of them. It is asked here rather than inside the run
because it is a property of a WHOLE cell: no unit can see a sibling, so no unit
can report it, and the merge that first encounters it is the thing the answer
is supposed to explain rather than a witness to it.

Divergence is counted per MODULE PATH, and only for a path more than one unit
wrote. A module exactly one unit owns cannot disagree with anybody, and folding
those in would bury the measurement under the many files each cell writes once:
in the recorded corpus 11 of 11 SHARED modules disagreed, and the same run's
per-file agreement rate reads near-perfect because most files are written once.

What counts as agreement is the module's PUBLIC SURFACE, not its bytes.
Two units are supposed to write different bodies for a module they share, and
a byte comparison would report the intended division of labour as the defect.
What must match is the set of names a sibling can import: a function present in
one copy and absent from the other is what makes the merge choose, and a
signature that changed arity is what makes the chosen one wrong.
"""

import ast
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger

logger = get_logger(__name__)

#: How many units must have written a module before it can disagree with
#: anybody. One unit owning a file is the ordinary case and carries no
#: information; counting those buries the finding under the many files a cell
#: writes once, which is how a per-file agreement rate reads near-perfect while
#: every shared seam is broken.
_SHARED: Final[int] = 2


@dataclass(frozen=True, slots=True)
class Surface:
    """The public names one module exposes, and how they are spelled.

    Attributes:
        names: Every public top-level name, sorted.
        signatures: Each public function mapped to its parameter names, in
            order. Held apart from ``names`` because the two fail differently:
            a missing name breaks the import, and a changed parameter list
            breaks the call that the import made possible.
    """

    names: tuple[str, ...]
    signatures: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ModuleDivergence:
    """One module path more than one unit wrote, and whether they agree.

    Attributes:
        path: The module, relative to each unit's project tree.
        units: The units that wrote it, sorted.
        agreed: Whether every copy exposes the same public names with the same
            signatures.
        missing_names: Names present in some copies and absent from others.
        conflicting_signatures: Functions every copy declares but not with the
            same parameters.
    """

    path: str
    units: tuple[str, ...]
    agreed: bool
    missing_names: tuple[str, ...] = ()
    conflicting_signatures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CellDivergence:
    """What one cell's units agreed and disagreed on.

    Attributes:
        modules: Every shared module, in path order.
        unreadable: Paths that could not be parsed, so nothing is claimed
            about them. Reported rather than skipped: a unit that left a file
            of prose where a module belongs is a finding, and silently
            dropping it would report better agreement than the tree holds.
    """

    modules: tuple[ModuleDivergence, ...]
    unreadable: tuple[str, ...] = ()

    @property
    def shared(self) -> int:
        """How many modules more than one unit wrote.

        Returns:
            The count, which is the denominator the headline is read against.
        """
        return len(self.modules)

    @property
    def diverged(self) -> int:
        """How many of those the units disagreed on.

        Returns:
            The count.
        """
        return sum(not module.agreed for module in self.modules)

    def headline(self) -> str:
        """The one line worth putting beside a cell's score.

        Returns:
            The summary.
        """
        if not self.modules:
            return "no module was written by more than one unit"
        return (
            f"{self.diverged} of {self.shared} shared modules disagree on their "
            f"public surface"
        )


def read_surface(source: str) -> Surface:
    """Take the public surface of one module.

    Only TOP-LEVEL names, and only public ones. A method is reached through
    its class, so a class that agrees on its name and differs inside is one
    the importing sibling still compiles against; an underscore name is not
    something a sibling is entitled to reach for at all.

    Args:
        source: The module's text.

    Returns:
        Its public surface.

    Raises:
        SyntaxError: The text does not parse as Python.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    signatures: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() if not node.name.startswith(
                "_"
            ):
                names.add(node.name)
                signatures[node.name] = _parameters(node.args)
            case ast.ClassDef() if not node.name.startswith("_"):
                names.add(node.name)
            case ast.Assign():
                names.update(
                    target.id
                    for target in node.targets
                    if isinstance(target, ast.Name) and not target.id.startswith("_")
                )
            case ast.AnnAssign(target=ast.Name(id=name)) if not name.startswith("_"):
                names.add(name)
            case _:
                continue
    return Surface(names=tuple(sorted(names)), signatures=signatures)


def _parameters(args: ast.arguments) -> tuple[str, ...]:
    """Name every parameter a function declares, in order.

    Names rather than annotations, because a sibling calls by name and a unit
    re-spelling a type it cannot see is not the divergence being measured.

    Returns:
        The parameter names.
    """
    return tuple(
        argument.arg
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        if argument.arg != "self"
    )


def compare(surfaces: Mapping[str, Surface]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Say how a module's copies disagree.

    Args:
        surfaces: Each unit mapped to the surface it wrote for one module.

    Returns:
        The names not every copy exposes, and the functions every copy exposes
        with different parameters.
    """
    everywhere = set.intersection(*(set(one.names) for one in surfaces.values()))
    anywhere: set[str] = set()
    anywhere.update(*(set(one.names) for one in surfaces.values()))
    conflicting = {
        name
        for name in everywhere
        if len({one.signatures.get(name) for one in surfaces.values()}) > 1
    }
    return tuple(sorted(anywhere - everywhere)), tuple(sorted(conflicting))


def measure(unit_trees: Mapping[str, Path]) -> CellDivergence:
    """Compare every module more than one of *unit_trees* wrote.

    Args:
        unit_trees: Each unit's label mapped to its project directory.

    Returns:
        What the cell's units agreed and disagreed on.
    """
    written: dict[str, dict[str, Surface]] = defaultdict(dict)
    unreadable: list[str] = []
    for unit, tree in unit_trees.items():
        for module in sorted(tree.rglob("*.py")):
            relative = module.relative_to(tree).as_posix()
            try:
                written[relative][unit] = read_surface(
                    module.read_text(encoding="utf-8", errors="replace")
                )
            except SyntaxError:
                unreadable.append(f"{unit}:{relative}")
    modules: list[ModuleDivergence] = []
    for path, surfaces in sorted(written.items()):
        if len(surfaces) < _SHARED:
            continue
        missing, conflicting = compare(surfaces)
        modules.append(
            ModuleDivergence(
                path=path,
                units=tuple(sorted(surfaces)),
                agreed=not missing and not conflicting,
                missing_names=missing,
                conflicting_signatures=conflicting,
            )
        )
    return CellDivergence(modules=tuple(modules), unreadable=tuple(sorted(unreadable)))


def leaf_trees(work_root: Path, cell_key: str) -> dict[str, Path]:
    """Find every leaf tree a kept recording left for one cell.

    Reads the directory layout ``unit_workspace`` writes rather than a journal,
    because the journal records what a unit COST and this asks what it WROTE,
    and a cell killed part-way leaves trees for units it never journalled.

    Args:
        work_root: The recording's scratch root.
        cell_key: The cell whose units are wanted.

    Returns:
        Each leaf's key mapped to its project directory, empty when the
        recording did not keep its workspaces.
    """
    cell = work_root / cell_key
    if not cell.is_dir():
        return {}
    found: dict[str, Path] = {}
    for unit in sorted(cell.iterdir()):
        if not unit.name.startswith("leaf-"):
            continue
        project = unit / "projects"
        if not project.is_dir():
            continue
        for tree in project.iterdir():
            if tree.is_dir():
                found[unit.name] = tree
    return found


def render(divergence: CellDivergence, *, limit: int = 20) -> Sequence[str]:
    """Word the finding for a person reading a recording.

    Args:
        divergence: What was measured.
        limit: The most modules to name individually.

    Returns:
        The lines to print.
    """
    lines = [divergence.headline()]
    for module in divergence.modules[:limit]:
        mark = "ok  " if module.agreed else "DIFF"
        lines.append(f"  {mark} {module.path}  ({len(module.units)} units)")
        if module.missing_names:
            missing = ", ".join(module.missing_names)
            lines.append(f"         missing from some: {missing}")
        if module.conflicting_signatures:
            lines.append(
                f"         signature conflict: "
                f"{', '.join(module.conflicting_signatures)}"
            )
    if len(divergence.modules) > limit:
        lines.append(f"  ... and {len(divergence.modules) - limit} more")
    if divergence.unreadable:
        lines.append(f"  unparseable: {', '.join(divergence.unreadable)}")
    return lines


__all__ = [
    "CellDivergence",
    "ModuleDivergence",
    "Surface",
    "compare",
    "leaf_trees",
    "measure",
    "read_surface",
    "render",
]

"""Do the units of a recorded cell agree on the names they share?

Run over the kept workspaces of one or more recordings, so a run with a
contract stage can be read against the corpus recorded without one. Reads only
what is on disk and makes no provider call, so it costs nothing and can be
re-run over an old recording whenever the measure itself changes.

    python scripts/report_interface_divergence.py .recursion-depth/work/run-*

Answers per cell, because divergence is a property of a whole cell: no unit can
see a sibling, so no unit can report it, and the merge that first meets it is
the thing being explained rather than a witness to it.
"""

import argparse
import sys
from pathlib import Path

# `evals` lives at the repository root rather than on the interpreter's path,
# and this runs as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.recursion_depth.divergence import leaf_trees, measure, render


def _cells(work_root: Path) -> list[str]:
    """Name every cell a recording left trees for.

    Returns:
        The cell keys, sorted.
    """
    return sorted(
        entry.name
        for entry in work_root.iterdir()
        if entry.is_dir() and entry.name not in {"transcripts", "host"}
    )


def main(argv: list[str] | None = None) -> int:
    """Report divergence for every cell of every named recording.

    Returns:
        0 when at least one cell could be measured, 1 when none could.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "work_roots",
        nargs="+",
        type=Path,
        help="Recording scratch roots, each holding one or more cell directories.",
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Most modules to name individually."
    )
    args = parser.parse_args(argv)
    if args.limit < 0:
        # A negative limit reaches `render` as a negative slice bound, which
        # hides modules from the end of the list while the omitted count is
        # computed against the whole: `--limit -1` names all but one and
        # reports one more omitted than there are modules.
        parser.error("--limit must be non-negative")

    measured = 0
    for work_root in args.work_roots:
        if not work_root.is_dir():
            print(f"{work_root}: not a directory")
            continue
        for cell_key in _cells(work_root):
            trees = leaf_trees(work_root, cell_key)
            print(f"\n=== {work_root.name} / {cell_key}: {len(trees)} leaf trees")
            if not trees:
                print("  no leaf tree was kept")
                continue
            measured += 1
            for line in render(measure(trees), limit=args.limit):
                print(line)
    return 0 if measured else 1


if __name__ == "__main__":
    sys.exit(main())

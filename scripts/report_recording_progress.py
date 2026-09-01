"""What every recording under ``results/`` has finished so far.

A cell is hours of paid provider work and its report is written only at the
end, so the question "is this one going anywhere" has no answer in the report
directory until it is too late to act on. The session journal DOES hold it, one
row per finished unit, which is what this reads.

Deliberately a report over what is on disk rather than anything live: it takes
no lock, opens no socket, and reads a file the recorder only ever appends to,
so running it against four in-flight recordings cannot perturb any of them.

    python scripts/report_recording_progress.py
    python scripts/report_recording_progress.py --only sweep-

A truncated LAST line is expected and skipped: the recorder flushes and fsyncs
each row, so a kill lands mid-line, and refusing to read the file for that
reason would withhold every row already paid for.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# `evals` lives at the repository root rather than on the interpreter's path,
# and this runs as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.harness.rendering import one_line
from synthorg.engine.loop_protocol import TerminationReason

#: Where recordings are written, one directory each.
RESULTS: Final[Path] = Path("evals/recursion_depth/results")

#: The per-session journal inside one.
PROGRESS: Final[str] = "progress.jsonl"

#: Endings where the loop stopped the agent rather than the agent finishing.
#: Taken off the product's own enum rather than matched on a substring: the
#: values are what the recorder wrote, and guessing at their spelling is how a
#: reader comes to report zero of something that is happening on every row.
CUT_OFF: Final[frozenset[str]] = frozenset(
    {
        TerminationReason.BUDGET_EXHAUSTED.value,
        TerminationReason.MAX_TURNS.value,
        TerminationReason.STAGNATION.value,
    }
)


@dataclass(frozen=True, slots=True)
class Recording:
    """One recording directory's state, as its journal reports it.

    Attributes:
        name: The directory's name.
        sessions: How many units have finished.
        tokens: What those units spent, added up. The cell rows add the same
            sessions a second time, so the two are never summed together.
        delivered: How many finished units delivered what they claimed.
        exhausted: How many stopped on their token ceiling rather than
            finishing, which is the shape a budget problem takes.
        kinds: Session count per unit kind, so a cell stuck before its leaves
            reads differently from one stuck in its merge.
        broken: Rows that could not be read, excluding a truncated last one.
    """

    name: str
    sessions: int
    tokens: int
    delivered: int
    exhausted: int
    kinds: dict[str, int]
    broken: int


def _rows(path: Path) -> tuple[list[dict[str, object]], int]:
    """Every readable row of one journal, and how many were not.

    Returns:
        The rows after the header, and the count of unreadable ones. A broken
        LAST line is a kill landing mid-write and is not counted; a broken line
        anywhere earlier is corruption and is.
    """
    # ``errors="replace"`` because the tolerance this function promises is
    # per-LINE and a strict decode is per-FILE. A kill lands mid-write, so the
    # expected damage is a truncated multi-byte character at EOF, which a
    # strict read raises on for the whole file: every row already paid for is
    # discarded, and the caller cannot tell that from a journal that has yet
    # to record anything. Replaced, the damage stays confined to the last
    # line, where the rule below already handles it.
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        # Counted, never returned as an empty journal: a recording with hours
        # behind it must not render as a clean row of zeros because the reader
        # could not open it.
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
        return [], 1
    rows: list[dict[str, object]] = []
    broken = 0
    for index, line in enumerate(lines[1:], start=1):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                broken += 1
            continue
        # A line that parses to something other than an object is schema
        # drift, not a truncated write, so it is counted wherever it sits.
        if isinstance(parsed, dict):
            rows.append(parsed)
        else:
            broken += 1
    return rows, broken


def read(directory: Path) -> Recording | None:
    """Summarise one recording directory.

    Returns:
        Its state, or ``None`` when it holds no session journal at all.
    """
    journal = directory / PROGRESS
    if not journal.is_file():
        return None
    rows, broken = _rows(journal)
    kinds: dict[str, int] = {}
    tokens = 0
    delivered = 0
    exhausted = 0
    for row in rows:
        unit = row.get("unit")
        if not isinstance(unit, dict):
            # A row the reader cannot interpret is a session it cannot report,
            # and silently dropping it understates a recording's spend.
            broken += 1
            continue
        kind = str(unit.get("kind", "?"))
        kinds[kind] = kinds.get(kind, 0) + 1
        spent = unit.get("tokens")
        if isinstance(spent, int):
            tokens += spent
        if unit.get("delivered"):
            delivered += 1
        terminations = unit.get("terminations")
        if isinstance(terminations, list) and any(
            str(one) in CUT_OFF for one in terminations
        ):
            exhausted += 1
    return Recording(
        name=directory.name,
        sessions=sum(kinds.values()),
        tokens=tokens,
        delivered=delivered,
        exhausted=exhausted,
        kinds=kinds,
        broken=broken,
    )


def render(recordings: list[Recording]) -> str:
    """Lay the recordings out as one table.

    Returns:
        The table, newest-spending first.
    """
    if not recordings:
        return "no recording holds a session journal"
    header = (
        f"{'recording':34} {'sess':>5} {'tokens':>12} {'deliv':>6} "
        f"{'exh':>4}  breakdown"
    )
    lines = [header, "-" * len(header)]
    for one in sorted(recordings, key=lambda r: -r.tokens):
        breakdown = " ".join(f"{k}:{v}" for k, v in sorted(one.kinds.items()))
        if one.broken:
            breakdown = f"{breakdown}  [{one.broken} unreadable]"
        lines.append(
            f"{one.name:34} {one.sessions:5d} {one.tokens:12,d} "
            f"{one.delivered:6d} {one.exhausted:4d}  {breakdown}"
        )
    return "\n".join(lines)


def detail(directory: Path) -> str:
    """Every finished unit of one recording, one line each.

    The summary answers "is this going anywhere"; this answers "what did it
    do", which is the question a delivery count raises rather than settles: the
    artifact contract is all-or-nothing, so one missing declared path marks a
    whole unit as having delivered nothing, and the row that says so sits
    beside the paths it wanted.

    Returns:
        The lines, in the order the units finished.
    """
    rows, _ = _rows(directory / PROGRESS)
    lines = [f"# {directory.name}"]
    for row in rows:
        unit = row.get("unit")
        if not isinstance(unit, dict):
            continue
        missing = unit.get("missing_declared_paths") or []
        changed = unit.get("workspace_files_changed")
        # Guarded per field, as `read` does and for the same reason: a journal
        # row is whatever survived the run that wrote it, and an unguarded
        # `int()` on a non-numeric string or `len()` on a scalar ends the whole
        # --detail report on one damaged row.
        spent = unit.get("tokens")
        claimed = unit.get("claimed")
        ends = unit.get("terminations")
        lines.append(
            f"  {unit.get('kind', '?')!s:8} "
            f"{unit.get('unit_id', '?')!s:32} "
            f"{(spent if isinstance(spent, int) else 0):9,d}tok "
            f"turns={unit.get('turns')!s:>3} "
            f"files={changed!s:>4} "
            f"deliv={unit.get('delivered')!s:<5} "
            f"claims={len(claimed) if isinstance(claimed, list) else 0} "
            f"end={','.join(str(one) for one in ends) if isinstance(ends, list) and ends else '-'}"
        )
        if missing:
            # A declared path is whatever a planner wrote down, so it reaches
            # this terminal as agent-authored text: flattened, or a newline
            # or control sequence in one breaks the row it is printed under.
            paths = ", ".join(one_line(str(one)) for one in missing)
            lines.append(f"      missing: {paths}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print every recording's progress.

    Returns:
        0 always: a recording with nothing in it yet is a state, not an error.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results", type=Path, default=RESULTS, help="Where recordings live."
    )
    parser.add_argument(
        "--only", default=None, help="Only directories whose name contains this."
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="One line per finished unit rather than one per recording.",
    )
    args = parser.parse_args(argv)

    wanted = [
        directory
        for directory in sorted(args.results.iterdir())
        if directory.is_dir() and (args.only is None or args.only in directory.name)
        if (directory / PROGRESS).is_file()
    ]
    if args.detail:
        print("\n".join(detail(directory) for directory in wanted))
        return 0
    print(render([one for one in map(read, wanted) if one is not None]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

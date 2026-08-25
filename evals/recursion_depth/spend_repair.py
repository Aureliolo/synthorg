# module-kind: code
"""Rebuild a recording's spend column from the per-call log.

A sweep's session rows ARE its spend ledger and its only one, so a session that
ran and recorded nothing is spend that happened and was never written down. That
is what a concurrent recording produced: ``open_run_ledger`` installed each
session's tracker as a process-wide field and swapped it per session, so with
several leaves in flight the last one installed collected everyone's records and
the rest collected none. 42 of 129 leaf sessions journalled zero tokens after
running up to 56 turns, and the total understated by about a quarter.

The fault is fixed at source (each session now filters one per-cell tracker by
its own task id) and this repairs what the broken runs already wrote, because
every call ALSO emitted ``cost.recorded`` carrying its task id and both token
counts. That account is written per CALL rather than collected per session, so
no swap can scramble it.

Attribution is the whole difficulty, and a task id alone will not do it. The
root merge's task id is derived from the specification (``uuid5`` over the spec
id, deliberately, "so two runs of one spec are attributable to one root"), so
EVERY cell's root assembly carries the same one; joining on it adds all the root
spend of every cell to each of them. Within a cell the ids are unique, and cells
run one at a time, so the id becomes unique once the log is cut into intervals:
``record_journalled`` fires once per unit, and a unit's spend is everything
banked against its id since that same id was last journalled.
"""

import collections
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final

from evals.recursion_depth.journal import cell_key
from evals.recursion_depth.models import CellRecord
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_SPEND_REPAIRED,
    EVALS_RECURSION_SPEND_UNCLAIMED,
)

logger = get_logger(__name__)

#: Terminal colour codes sit BETWEEN each key and its value, so a match against
#: the raw line finds nothing and every unit reads as unattributable.
_ANSI: Final = re.compile(r"\x1b\[[0-9;]*m")

_TASK: Final = re.compile(r"task_id=([0-9a-f-]+)")
_INPUT: Final = re.compile(r"input_tokens=(\d+)")
_OUTPUT: Final = re.compile(r"output_tokens=(\d+)")
_JOURNALLED: Final = re.compile(r"cell=([^/\s]+)/(\S+)")

#: The per-call cost event, and the one category that is a call of its own.
_COST_EVENT: Final = "cost.recorded"
_PRODUCTIVE: Final = "call_category=productive"

#: Emitted once per unit the moment its row is journalled.
_JOURNAL_EVENT: Final = "record_journalled"

#: Stated in the report, because a repaired figure is a provenance claim. A
#: reader comparing this run's spend against another's is entitled to know one
#: column was reconstructed rather than collected.
SPEND_REPAIRED_CAVEAT: Final[str] = (
    "The token column was rebuilt from the recorder's per-call log. This "
    "recording predates the per-cell cost ledger, so concurrent leaf sessions "
    "swapped a process-wide sink and some journalled zero while others "
    "absorbed their records. The repair attributes each call from the log, "
    "which is written per call and cannot be scrambled by that swap; plan "
    "units keep their journalled figures, being the one kind of session that "
    "never ran concurrently. Session and attempt counts were never affected."
)


def _readable(path: Path) -> Iterator[str]:
    """Every line of *path*, with its colour codes removed.

    Yields:
        The decoded lines, in order.
    """
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            yield _ANSI.sub("", raw)


def tokens_by_unit(log: Path) -> Mapping[tuple[str, str], int]:
    """Attribute every productive call to the unit that was journalling it.

    Args:
        log: The recorder's own log, which holds one ``cost.recorded`` line per
            call and one ``record_journalled`` line per unit.

    Returns:
        Productive tokens keyed by ``(cell key, unit id)``, where the cell key
        is the one the journal wrote (``d<cap>-<arm>-r<repetition>``).
    """
    pending: collections.Counter[str] = collections.Counter()
    attributed: dict[tuple[str, str], int] = {}

    for line in _readable(log):
        if _COST_EVENT in line:
            if _PRODUCTIVE not in line:
                continue
            task, used_in, used_out = (
                _TASK.search(line),
                _INPUT.search(line),
                _OUTPUT.search(line),
            )
            if task and used_in and used_out:
                pending[task.group(1)] += int(used_in.group(1)) + int(used_out.group(1))
            continue
        if _JOURNAL_EVENT not in line:
            continue
        found = _JOURNALLED.search(line)
        if found is None:
            continue
        # Popping is what separates a repeated id: the balance standing against
        # it belongs to the unit being journalled NOW, and the next cell's root
        # merge starts from nothing.
        attributed[found.group(1), found.group(2)] = pending.pop(found.group(2), 0)

    if pending:
        # Never silent. A call attributed to no unit is spend this repair lost,
        # which is the fault it exists to correct.
        logger.warning(
            EVALS_RECURSION_SPEND_UNCLAIMED,
            unclaimed_tasks=len(pending),
            unclaimed_tokens=sum(pending.values()),
            log=str(log),
        )
    logger.info(
        EVALS_RECURSION_SPEND_REPAIRED,
        attributed_units=len(attributed),
        attributed_tokens=sum(attributed.values()),
        log=str(log),
    )
    return attributed


def repair_cell_spend(
    cells: Sequence[CellRecord], attributed: Mapping[tuple[str, str], int]
) -> list[CellRecord]:
    """Replace each unit's token figure with the log's account of the same calls.

    A unit the log cannot see keeps what it journalled. That is the PLAN unit
    and only the plan unit: planning is not a task, so it carries no task id the
    log knows, and it ran on its own tracker sequentially, which is the one kind
    of session the race never touched. Overwriting those with a zero would erase
    the figures that are already right.

    Args:
        cells: The recorded cells, unchanged.
        attributed: Tokens per ``(cell key, unit id)`` from
            :func:`tokens_by_unit`.

    Returns:
        The cells, with leaf and merge token figures replaced.
    """
    repaired: list[CellRecord] = []
    for cell in cells:
        key = cell_key(cell.depth_cap, cell.arm, cell.repetition)
        units = tuple(
            unit
            if (found := attributed.get((key, unit.unit_id))) is None
            else unit.model_copy(update={"tokens": found})
            for unit in cell.units
        )
        repaired.append(cell.model_copy(update={"units": units}))
    return repaired


__all__ = ["SPEND_REPAIRED_CAVEAT", "repair_cell_spend", "tokens_by_unit"]

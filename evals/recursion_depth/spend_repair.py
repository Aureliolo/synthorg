# module-kind: code
"""Rebuild a recording's spend column from the per-call log.

A sweep's session rows ARE its spend ledger and its only one, so a session that
ran and recorded nothing is spend that happened and was never written down. That
is what a concurrent recording produced: ``open_run_ledger`` installed each
session's tracker as a process-wide field and swapped it per session, so with
several leaves in flight the last one installed collected everyone's records and
the rest collected none. In the committed recording 59 of 183 leaf sessions
journalled zero tokens, the worst of them after running 56 turns.

This module repairs a recording whose sessions shared one process-wide sink
swapped per session. It has nothing to repair for a recording whose sessions
instead filter a shared per-cell tracker by their own task id (``ledger_scope``
in :mod:`evals.recursion_depth.session`), which that race cannot reach. What
makes the repair possible either way is that every call ALSO emitted
``cost.recorded`` carrying its task id and both token counts: an account written
per CALL rather than collected per session, which no swap can scramble.

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
from evals.recursion_depth.models import PLAN_UNIT_SUFFIX, CellRecord
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_SPEND_LOG_MALFORMED,
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
#: One journal writes two row shapes and both log the same event. A UNIT row
#: keys on ``<cell>/<unit>``; the whole-CELL row that follows a cell's units
#: keys on the cell alone. Reading the key first is what tells the second from
#: a line the parser cannot read at all.
_JOURNAL_KEY: Final = re.compile(r"cell=(\S+)")
_JOURNALLED: Final = re.compile(r"^([^/]+)/(.+)$")

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
    "recording's sessions shared one process-wide cost sink swapped per "
    "session, so concurrent leaves could journal zero while a neighbour "
    "absorbed their records. The repair attributes each call from the log, "
    "which is written per call and cannot be scrambled by that swap, cutting "
    "a repeated task id into intervals at the point each unit was journalled. "
    "The money column is untouched: every connection here is flat-rate, so it "
    "reads zero throughout. Session and attempt counts were never affected."
)


def _readable(path: Path) -> Iterator[str]:
    """Every line of *path*, with its colour codes removed.

    Yields:
        The decoded lines, in order.
    """
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            yield _ANSI.sub("", raw)


def _banked(line: str, pending: collections.Counter[str]) -> bool:
    """Bank one productive call's tokens against the task that made it.

    Args:
        line: A ``cost.recorded`` line, already decoloured.
        pending: The running balance per task id, mutated in place.

    Returns:
        Whether the line could be read at all.
    """
    if _PRODUCTIVE not in line:
        return True
    task, used_in, used_out = (
        _TASK.search(line),
        _INPUT.search(line),
        _OUTPUT.search(line),
    )
    if not (task and used_in and used_out):
        return False
    pending[task.group(1)] += int(used_in.group(1)) + int(used_out.group(1))
    return True


def _claimed(
    line: str,
    pending: collections.Counter[str],
    attributed: dict[tuple[str, str], int],
) -> bool:
    """Close one unit's interval, attributing what stands against it.

    Args:
        line: A ``record_journalled`` line, already decoloured.
        pending: The running balance per task id, mutated in place.
        attributed: Tokens per ``(cell key, unit id)``, mutated in place.

    Returns:
        Whether the line could be read at all.
    """
    key = _JOURNAL_KEY.search(line)
    if key is None:
        return False
    found = _JOURNALLED.match(key.group(1))
    if found is None:
        # The whole-cell row, which follows the cell's units and carries no
        # unit of its own. Nothing to attribute and nothing wrong.
        return True
    cell_key, unit_id = found.group(1), found.group(2)
    if unit_id.endswith(PLAN_UNIT_SUFFIX):
        # Planning dispatches under the tree's ROOT task id, which the root
        # merge later reuses, while its journalled id is minted and matches no
        # task at all. Popping here is what stops the whole tree's planning
        # spend riding forward onto that merge: measured across the six
        # recorded cells, what stands when a plan row lands is one id holding
        # exactly what the plan journalled, planning being the first thing a
        # cell runs.
        attributed[cell_key, unit_id] = sum(pending.values())
        pending.clear()
        return True
    if unit_id not in pending:
        # No call recorded under this id. An entry here would read as a
        # measured zero and overwrite a figure the log cannot contradict.
        return True
    # Popping is what separates a repeated id: the balance standing against it
    # belongs to the unit being journalled NOW, and the next cell's root merge
    # starts from nothing.
    attributed[cell_key, unit_id] = pending.pop(unit_id)
    return True


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
    malformed = 0

    for line in _readable(log):
        if _COST_EVENT in line:
            malformed += not _banked(line, pending)
        elif _JOURNAL_EVENT in line:
            malformed += not _claimed(line, pending, attributed)

    if malformed:
        # A line this parser cannot read is a call it cannot place, which is
        # the same loss the repair exists to undo rather than a parsing detail.
        logger.error(
            EVALS_RECURSION_SPEND_LOG_MALFORMED,
            malformed_lines=malformed,
            log=str(log),
        )
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

    A unit absent from *attributed* keeps what it journalled, which is why
    :func:`tokens_by_unit` must not enter a unit it never saw a call for: an
    entry reads as a measured zero and there is no way back from one. Every
    plan unit in the first recorded sweep was overwritten that way while its
    tokens rode forward onto the merge sharing its task id, and the per-cell
    totals still added up, because the two errors were equal and opposite.

    Args:
        cells: The recorded cells, unchanged.
        attributed: Tokens per ``(cell key, unit id)`` from
            :func:`tokens_by_unit`.

    Returns:
        The cells, with each attributed unit's token figure replaced.
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

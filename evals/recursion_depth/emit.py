# module-kind: code
"""Write the report: JSON, Markdown and the chart.

Three files rather than one because they answer different questions. The JSON is
what a later analysis reads; the Markdown is what a person reads; the SVG is the
one thing that gets pasted somewhere else, which is why its caveats are drawn
into it rather than left in the prose beside it.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Final, get_args

from pydantic import BaseModel, JsonValue

from evals.errors import (
    RecursionDepthClaimUnresolvableError,
    RecursionDepthNoCellsMeasuredError,
)
from evals.recursion_depth.chart import render_chart
from evals.recursion_depth.journal import cell_key
from evals.recursion_depth.manifest import Arm, ModelPair
from evals.recursion_depth.models import (
    MERGE,
    UNATTRIBUTED_LEAVES_CAVEAT,
    UNPRICED_COST_CAVEAT,
    UNRESOLVABLE_CLAIM_CELLS_CAVEAT,
    UNRESOLVED_CLAIMS_CAVEAT,
    CellRecord,
    CostBasis,
    DepthPoint,
    DepthSpread,
    Provenance,
    RecursionDepthReport,
    SpendSource,
    SurvivalPoint,
    UnitRecord,
    sum_costs,
)
from evals.recursion_depth.score import (
    achieved_depth_histogram,
    curve_by_achieved_depth,
    curve_by_depth_cap,
    spread_by_achieved_depth,
    spread_by_depth_cap,
    survival_by_achieved_depth,
    survival_by_depth_cap,
    unjudged_by_achieved_depth,
)
from evals.recursion_depth.spend_repair import SPEND_REPAIRED_CAVEAT
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_RECURSION_NO_CELLS,
    EVALS_RECURSION_REPORT_EMITTED,
)

logger = get_logger(__name__)

#: What an unavailable cell's reason starts with when a claim named nothing.
#: Derived from the class rather than spelled out, so renaming the error keeps
#: the caveat firing instead of silently ceasing to.
_CLAIM_UNRESOLVABLE: Final[str] = RecursionDepthClaimUnresolvableError.__name__

#: The three artifacts a report is, named once. A re-score reads the JSON back
#: for what only it holds, so a second literal spelling would be one rename
#: from reading a file nothing writes.
REPORT_JSON_NAME: Final[str] = "depth_curve.json"
REPORT_MARKDOWN_NAME: Final[str] = "depth_curve.md"
REPORT_CHART_NAME: Final[str] = "chart.svg"


def derived_caveats(
    cells: Sequence[CellRecord],
    *,
    spend_source: SpendSource,
    cost_basis: CostBasis = CostBasis.PRICED,
) -> list[str]:
    """The caveats a recording implies on its own.

    Separated from the assembler so the caller owns the final list. A re-score
    carries the original report's caveats forward verbatim, which is the only
    way the run-state ones survive (the ceiling and quota caveats are appended
    while the loop runs and are recoverable from nowhere else), and an
    assembler appending its own on top would emit this line twice.

    Args:
        cells: Every recorded cell.
        spend_source: What the recording says its token column is. Read from
            the journal rather than from whoever typed a flag, because a claim
            about the figures a reader is holding has to survive being
            re-scored by somebody else.
        cost_basis: Whether this recording's cost figures are money or an
            honest absence of it. Defaults to ``PRICED`` for the same reason
            ``Provenance.cost_basis`` does: a recording made before this field
            existed carried a real cost throughout.

    Returns:
        The caveats this recording implies, which may be none.
    """
    blank = sum(
        1 for point in survival_by_achieved_depth(cells) if point.delivered_claims == 0
    )
    dropped = sum(unit.unresolved_claims for cell in cells for unit in cell.units)
    stopped = sum(
        1
        for cell in cells
        if (cell.unavailable_reason or "").startswith(_CLAIM_UNRESOLVABLE)
    )
    return [
        *([UNATTRIBUTED_LEAVES_CAVEAT.format(buckets=blank)] if blank else []),
        *([UNRESOLVED_CLAIMS_CAVEAT.format(dropped=dropped)] if dropped else []),
        *([UNRESOLVABLE_CLAIM_CELLS_CAVEAT.format(cells=stopped)] if stopped else []),
        *([SPEND_REPAIRED_CAVEAT] if spend_source is SpendSource.REPAIRED else []),
        *([UNPRICED_COST_CAVEAT] if cost_basis is CostBasis.UNPRICED else []),
    ]


def assemble_report(
    *,
    provenance: Provenance,
    cells: Sequence[CellRecord],
    caveats: Sequence[str],
    planned_cells: int,
) -> RecursionDepthReport:
    """Build the report from cells that are already measured and journalled.

    The single owner of how a report is shaped, so the recorder and the
    re-score cannot drift apart while both keep producing a valid report.

    Here rather than in the runner because it is a property of the ARTIFACT
    rather than of the run: it takes cells that are already on disk and calls no
    provider. ``check_recording_harness_journalled`` treats a call to this as
    assembling a report, so a driver still cannot reach a report without
    journalling the cells that paid for it.

    Args:
        provenance: What the sweep was measured against. Supplies the
            requirement count both curves divide by.
        cells: Every recorded cell, measured or unavailable.
        caveats: The final list, already complete. Nothing is appended here.
        planned_cells: How many cells the matrix asked for, for the log line
            below. A re-score passes the recorded count, since everything on
            disk was by construction planned.

    Returns:
        The assembled report.

    Raises:
        RecursionDepthNoCellsMeasuredError: Every run is unavailable.
    """
    measured = tuple(record for record in cells if record.achieved_depth is not None)
    if not measured:
        msg = (
            "the recursion-depth sweep measured no cells; every run is "
            "unavailable, and a report of those is not a curve"
        )
        logger.warning(
            EVALS_RECURSION_NO_CELLS,
            planned_cells=planned_cells,
            recorded_cells=len(cells),
        )
        raise RecursionDepthNoCellsMeasuredError(msg)
    required = provenance.requirement_count
    # A cell whose gate rendered no verdict is a missing observation, not a
    # gated one: excluded from the satisfaction curves alone. The oracle
    # grades survival and spread too, so exclusion there would be equally
    # defensible; it is scoped narrower because both are already thin lines
    # and thinning them further costs more than it buys. unjudged_by_depth
    # is a first-class field precisely so either curve can be recomputed
    # without it if that trade is ever revisited.
    judged = tuple(cell for cell in measured if not cell.is_unjudged)
    return RecursionDepthReport(
        provenance=provenance,
        cells=tuple(cells),
        by_achieved_depth=curve_by_achieved_depth(judged, requirement_count=required),
        by_depth_cap=curve_by_depth_cap(judged, requirement_count=required),
        survival_by_achieved_depth=survival_by_achieved_depth(measured),
        survival_by_depth_cap=survival_by_depth_cap(measured),
        spread_by_achieved_depth=spread_by_achieved_depth(
            measured, requirement_count=required
        ),
        spread_by_depth_cap=spread_by_depth_cap(measured, requirement_count=required),
        achieved_depth_histogram=achieved_depth_histogram(measured),
        unjudged_by_depth=unjudged_by_achieved_depth(measured),
        caveats=tuple(caveats),
    )


def write_report(report: RecursionDepthReport, out_dir: Path) -> tuple[Path, ...]:
    """Write the JSON, the Markdown and the chart.

    Args:
        report: The assembled report.
        out_dir: Directory the three artifacts are written to.

    Returns:
        The written paths, in the order JSON, Markdown, SVG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / REPORT_JSON_NAME
    md_path = out_dir / REPORT_MARKDOWN_NAME
    svg_path = out_dir / REPORT_CHART_NAME
    json_path.write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline=""
    )
    md_path.write_text(_markdown(report), encoding="utf-8", newline="")
    svg_path.write_text(
        render_chart(
            points=report.by_achieved_depth,
            caption_lines=_caption(report),
            by_cap=report.by_depth_cap,
            survival=report.survival_by_achieved_depth,
        ),
        encoding="utf-8",
        newline="",
    )
    logger.info(
        EVALS_RECURSION_REPORT_EMITTED,
        json_path=str(json_path),
        markdown_path=str(md_path),
        chart_path=str(svg_path),
        measured_cells=len(report.measured_cells),
        unavailable_cells=len(report.unavailable_cells),
    )
    return json_path, md_path, svg_path


def _caption(report: RecursionDepthReport) -> tuple[str, ...]:
    """The lines drawn into the chart itself.

    Returns:
        The caption lines.
    """
    histogram = ", ".join(
        f"{key} ({count})" for key, count in report.achieved_depth_histogram.items()
    )
    unjudged = ", ".join(
        f"depth {depth} ({count})" for depth, count in report.unjudged_by_depth.items()
    )
    excluded_note = (
        f"Excluded as unjudged (gate exhausted every round on a park): {unjudged}."
    )
    return (
        f"Runs reaching each depth: {histogram or 'none recorded'}.",
        *((excluded_note,) if unjudged else ()),
        *report.caveats,
    )


def _provenance_lines(report: RecursionDepthReport) -> list[str]:
    """Render what the sweep was measured against.

    Returns:
        The heading and the provenance bullets.
    """
    provenance = report.provenance
    return [
        "# Recursion-depth sweep",
        "",
        "Does verification at every merge hold off aggregation collapse as",
        "recursive decomposition deepens?",
        "",
        (
            f"- Measured against commit `{provenance.git_commit}`"
            f"{' (dirty tree)' if provenance.git_dirty else ''}"
        ),
        f"- Generated {provenance.generated_at.isoformat()}",
        f"- Manifest `{provenance.manifest_sha256}`",
        f"- Spec `{provenance.spec_id}`, {provenance.requirement_count} requirements",
        (
            f"- Executor `{provenance.executor.label}`, "
            f"reviewer `{provenance.reviewer.label}` "
            f"({provenance.independence.value})"
        ),
        # The sampling and reasoning depth each pair was bound to. Published
        # rather than left in the manifest because a reader holding the curve
        # is the one who needs to know what was asked of the models that
        # produced it. These are read off the dispatched binding, so `unset`
        # means the binding stated nothing and per-call resolution answered
        # for it, NOT that no value reached the provider.
        f"- Executor binding: {provenance.executor.sampling_summary}",
        f"- Reviewer binding: {provenance.reviewer.sampling_summary}",
        *(
            [f"- Sandbox image `{provenance.sandbox_image}`"]
            if provenance.sandbox_image is not None
            else []
        ),
        (
            f"- Total spend: {_cost_cell(report.total_cost)} across "
            f"{report.total_tokens} tokens "
            f"({provenance.spend_source.value})"
        ),
        "",
    ]


def _curve_sections(report: RecursionDepthReport) -> list[str]:
    """Render the two curves, four tables, in the order they are read.

    Returns:
        The curve sections.
    """
    return [
        "## Specification satisfied by depth reached",
        "",
        "What share of the specification the merged tree satisfies. Binned on",
        "the depth each tree actually reached, not on the cap its run was",
        "allowed: sweeping the cap does not sweep depth. This denominator is",
        "the same for every cell and cannot empty, so every run has a point,",
        "and it says nothing about where the work came from.",
        "",
        *_curve_table(report.by_achieved_depth),
        "",
        "## Specification satisfied by depth cap",
        "",
        "The manipulated variable, for comparison with the histogram below.",
        "",
        *_curve_table(report.by_depth_cap),
        "",
        "## Leaf-work survival by depth reached",
        "",
        "The question the sweep was built around: of the requirements the",
        "DELIVERED leaves claimed, how many the merged tree still satisfies.",
        "Same axis as the curve above, so the two read together. A bucket",
        "whose delivered leaves claimed nothing has no rate and reads `n/a`,",
        "which is not the same as a rate of zero.",
        "",
        *_survival_table(report.survival_by_achieved_depth),
        "",
        "## Leaf-work survival by depth cap",
        "",
        *_survival_table(report.survival_by_depth_cap),
        "",
        "## Per-depth spread",
        "",
        "Both curves above POOL a bucket's repetitions into one fraction, which",
        "is the right shape for a rate over work and cannot say whether a low",
        "point is one bad draw or a real drop. That is the question a cap is",
        "recorded more than once to answer, so the range and the middle run are",
        "reported here. The middle is the LOW median, so it is always a figure",
        "some run actually recorded rather than one describing none of them. A",
        "survival range reads `n/a` when no run in the bucket attributed",
        "anything, which is not the same as a rate of zero.",
        "",
        *_spread_table(report.spread_by_achieved_depth),
        "",
        "### The same, by depth cap",
        "",
        *_spread_table(report.spread_by_depth_cap),
        "",
        "## Every cell",
        "",
        "One row per run, which is the population behind every figure above.",
        "An unavailable cell is listed too, because it cost real money and",
        "leaving it out would make the matrix read as smaller than it was.",
        "",
        *_cell_table(report),
        "",
    ]


def _markdown(report: RecursionDepthReport) -> str:
    """Render the human-readable report.

    Returns:
        The Markdown text.
    """
    lines = [
        *_provenance_lines(report),
        *_curve_sections(report),
        "## How deep the runs went",
        "",
        *_histogram_table(report),
        "",
        "## What each arm spent, and what it bought",
        "",
        *_gate_table(report),
        "",
        *_unjudged_lines(report),
        "## Who judged whom",
        "",
        "The gate is the treatment, so a reviewer that came up on the executor's",
        "own binding would bias the result toward the null while every",
        "sweep-level field still read correctly. Every pairing that actually ran",
        "is listed, with the families the decorrelation claim rests on.",
        "",
        *_pairing_table(report),
        "",
        "## Every merge",
        "",
        "Both parties per merge, which is the grain the independence claim is",
        "made at. The same rows are in `depth_curve.json` under each cell's",
        "`units`.",
        "",
        "`Attempts ended` names how each assembling session stopped. A merge",
        "that delivered nothing because it was cut off at its budget and one",
        "that ran freely and assembled nothing are the same row in every other",
        "column, and only the first is a statement about the budget rather",
        "than about the work.",
        "",
        *_merge_table(report),
        "",
        "## Caveats",
        "",
        *(f"- {caveat}" for caveat in report.caveats),
        "",
    ]
    if report.unavailable_cells:
        lines.extend(["## Cells that could not be measured", ""])
        lines.extend(
            f"- depth cap {cell.depth_cap}, {cell.arm.value}, "
            f"repetition {cell.repetition}: {cell.unavailable_reason}"
            for cell in report.unavailable_cells
        )
        lines.append("")
    return "\n".join(lines)


def _curve_table(points: tuple[DepthPoint, ...]) -> list[str]:
    """Render one curve as a Markdown table.

    Returns:
        The table lines.
    """
    header = (
        "| Depth | Arm | Satisfied | Required | Fraction | Runs "
        "| Sessions | Tokens | Spend |"
    )
    rows = [header, "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for point in points:
        fraction = point.fraction
        # An absence rather than a zero: the bucket holds no run, so there is
        # no rate to report and printing 0.000 would claim a measured failure.
        rendered = "n/a" if fraction is None else f"{fraction:.3f}"
        rows.append(
            f"| {point.depth} | {point.arm.value} | {point.satisfied} "
            f"| {point.required} | {rendered} | {point.cells} "
            f"| {point.attempts} | {point.tokens} "
            f"| {_cost_cell(point.cost)} |"
        )
    return rows


def _survival_table(points: tuple[SurvivalPoint, ...]) -> list[str]:
    """Render one survival curve as a Markdown table.

    No spend columns: a run books what it cost once, on its specification
    point, and repeating the figure beside a second denominator would let two
    columns of one number come to disagree.

    Returns:
        The table lines.
    """
    rows = [
        "| Depth | Arm | Survived | Claimed | Fraction | Runs |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for point in points:
        fraction = point.fraction
        # An absence rather than a zero: the delivered leaves claimed nothing,
        # so there is no rate, and 0.000 would say every claim was lost.
        rendered = "n/a" if fraction is None else f"{fraction:.3f}"
        rows.append(
            f"| {point.depth} | {point.arm.value} | {point.surviving_claims} "
            f"| {point.delivered_claims} | {rendered} | {point.cells} |"
        )
    return rows


def _spread_table(rows: tuple[DepthSpread, ...]) -> list[str]:
    """Render one spread view as a Markdown table.

    Returns:
        The table lines.
    """
    header = (
        "| Depth | Arm | Runs | Satisfied (min..max) | Median | Required "
        "| Survival (min..max) | Median |"
    )
    table = [header, "|---:|---|---:|---|---:|---:|---|---:|"]
    table.extend(
        f"| {row.depth} | {row.arm.value} | {row.cells} "
        f"| {row.satisfied_min}..{row.satisfied_max} "
        f"| {row.satisfied_median} | {row.required} "
        f"| {_rate_range(row)} | {_rate(row.survival_median)} |"
        for row in rows
    )
    return table


def _rate_range(row: DepthSpread) -> str:
    """Render one bucket's survival range, absent rather than zero.

    Returns:
        The rendered range.
    """
    if row.survival_min is None or row.survival_max is None:
        return "n/a"
    return f"{row.survival_min:.3f}..{row.survival_max:.3f}"


def _rate(value: float | None) -> str:
    """Render one survival figure, absent rather than zero.

    Returns:
        The rendered figure.
    """
    return "n/a" if value is None else f"{value:.3f}"


def _cost_cell(value: float | None) -> str:
    """Render one cost figure, an absence rather than a zero when unpriced.

    ``None`` here means the connection that spent it does not price its
    calls, not that nothing was spent, and printing ``0.0000`` would claim
    the former is the latter.

    Returns:
        The rendered figure.
    """
    return "unpriced" if value is None else f"{value:.4f}"


def _cell_table(report: RecursionDepthReport) -> list[str]:
    """Render one row per recorded run.

    Built from ``report.cells`` rather than from a model of its own: the record
    already carries every column through its ``total_*`` fields, and a second
    holder of those figures is one that can come to disagree with them.

    Returns:
        The table lines.
    """
    table = [
        "| Cell | Achieved | Satisfied | Required | Sessions | Tokens | Spend |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    required = report.provenance.requirement_count
    for cell in report.cells:
        key = cell_key(cell.depth_cap, cell.arm, cell.repetition)
        achieved = (
            "unavailable" if cell.achieved_depth is None else str(cell.achieved_depth)
        )
        satisfied = (
            "n/a" if cell.achieved_depth is None else str(len(set(cell.merged_passing)))
        )
        table.append(
            f"| {key} | {achieved} | {satisfied} | {required} "
            f"| {cell.total_attempts} | {cell.total_tokens} "
            f"| {_cost_cell(cell.total_cost)} |"
        )
    return table


def _histogram_table(report: RecursionDepthReport) -> list[str]:
    """Render the achieved-depth histogram.

    Returns:
        The table lines.
    """
    rows = ["| Cap and depth reached | Runs |", "|---|---:|"]
    rows.extend(
        f"| {key} | {count} |" for key, count in report.achieved_depth_histogram.items()
    )
    return rows


type _Merge = tuple[CellRecord, UnitRecord]


def _merges_of(report: RecursionDepthReport) -> tuple[_Merge, ...]:
    """Every assembly the sweep ran, with the cell it belongs to.

    The single owner of that traversal, because three tables below ask the same
    question of it and a second walk is where two of them come to disagree about
    how many merges there were.

    Returns:
        Each merge unit paired with its cell, in recorded order.
    """
    return tuple(
        (cell, unit)
        for cell in report.measured_cells
        for unit in cell.units
        if unit.kind == MERGE
    )


def _gate_table(report: RecursionDepthReport) -> list[str]:
    """Render what each arm spent on merging and what it got for it.

    Sessions, tokens and spend sit beside the escalations because the arms are
    only comparable if their budgets were: repair in the gated arm alone would
    let it win by spending more rather than by catching anything, and the
    equal-budget claim is read here or nowhere.

    Returns:
        The table lines.
    """
    parked = dict.fromkeys(Arm, 0)
    amendments = dict.fromkeys(Arm, 0)
    merges = dict.fromkeys(Arm, 0)
    attempts = dict.fromkeys(Arm, 0)
    tokens = dict.fromkeys(Arm, 0)
    cost: dict[Arm, list[float | None]] = {arm: [] for arm in Arm}
    for cell, unit in _merges_of(report):
        merges[cell.arm] += 1
        # unit.parked reads only the LAST review, so a merge parked on
        # attempt 1 and approved on attempt 2 reads False there even though
        # a round genuinely escalated. parked_attempts counts every round
        # that did, which is the figure "how many escalations happened"
        # actually asks for.
        parked[cell.arm] += unit.parked_attempts
        amendments[cell.arm] += unit.amendments
        attempts[cell.arm] += unit.attempts
        tokens[cell.arm] += unit.tokens
        cost[cell.arm].append(unit.cost)
    rows = [
        (
            "| Arm | Merges | Sessions | Tokens | Spend | Parked escalations "
            "| Contract amendments |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    rows.extend(
        f"| {arm.value} | {merges[arm]} | {attempts[arm]} | {tokens[arm]} "
        f"| {_cost_cell(sum_costs(cost[arm]))} | {parked[arm]} | {amendments[arm]} |"
        for arm in Arm
    )
    return rows


def _unjudged_lines(report: RecursionDepthReport) -> list[str]:
    """Name the cells the primary curves excluded, and what they cost.

    Empty when nothing was excluded, so a sweep where the gate never starved
    prints nothing here: the section exists to make an exclusion visible, not
    to assert one happened.

    Returns:
        The section's lines, or an empty list.
    """
    if not report.unjudged_by_depth:
        return []
    excluded = tuple(cell for cell in report.measured_cells if cell.is_unjudged)
    total_cost = sum_costs(cell.total_cost for cell in excluded)
    depths = ", ".join(
        f"depth {depth}: {count}" for depth, count in report.unjudged_by_depth.items()
    )
    excluded_line = (
        f"it is left out of `by_achieved_depth` and `by_depth_cap`. {len(excluded)} "
        f"cell(s) were excluded this way ({depths}), costing "
        f"{_cost_cell(total_cost)} between them. They remain in `cells` and in "
        "the journal with their spend intact."
    )
    return [
        "**Excluded from the curve above**: a cell whose gate exhausted every",
        "repair round on a park is a missing observation, not a gated one, so",
        excluded_line,
        "",
    ]


def _pair_label(pair: ModelPair | None) -> str:
    """Render one party of a merge, family included.

    An absent pair is named rather than blanked: on the ungated arm nobody
    judged, which is the arm's definition, and on the gated arm it would mean a
    merge whose judge was not recorded at all.

    Returns:
        ``provider/model_id (family)``, or a stated absence.
    """
    if pair is None:
        return "none"
    return f"{pair.label} ({pair.family or 'family undeclared'})"


def _cell(value: str) -> str:
    """Render *value* so it stays inside the one table cell it belongs to.

    Every dynamic value on these rows is agent-authored (a unit title, a
    verdict) or operator-authored (a model id), so a ``|`` splits one merge
    into several cells and a newline splits it into several ROWS. The table's
    whole claim is one row per merge, and either character silently breaks it
    in a file whose only reader is a person.

    Args:
        value: The text to place in a cell.

    Returns:
        The text, safe to interpolate between two delimiters.
    """
    flattened = " ".join(value.splitlines())
    return flattened.replace("|", "\\|")


def _pairing_table(report: RecursionDepthReport) -> list[str]:
    """Render every ``(executor, reviewer)`` combination that actually ran.

    Read off the units rather than off the manifest, because the manifest says
    what the roster was ASKED to bind and these say what answered.

    Returns:
        The table lines.
    """
    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for cell, unit in _merges_of(report):
        pairing = (
            cell.arm.value,
            _cell(_pair_label(unit.executor)),
            _cell(_pair_label(unit.reviewer)),
        )
        counts[pairing] += 1
    rows = ["| Arm | Assembled by | Judged by | Merges |", "|---|---|---|---:|"]
    rows.extend(
        f"| {arm} | {executor} | {reviewer} | {count} |"
        for (arm, executor, reviewer), count in sorted(counts.items())
    )
    return rows


def _files_changed_cell(value: int | None) -> str:
    """Render the workspace delta, naming a recording that never asked.

    Returns:
        The count, or a stated absence for a pre-existing recording.
    """
    return "not recorded" if value is None else str(value)


def _merge_table(report: RecursionDepthReport) -> list[str]:
    """Render one row per merge, both parties named.

    Returns:
        The table lines.
    """
    rows = [
        (
            "| Cell | Depth | Assembly | Assembled by | Judged by | Verdict "
            "| Parked | Amendments | Delivered | Files changed | Attempts ended |"
        ),
        "|---|---:|---|---|---|---|---|---:|---|---:|---|",
    ]
    rows.extend(
        f"| {cell_key(cell.depth_cap, cell.arm, cell.repetition)} | {unit.depth} "
        f"| {_cell(unit.title)} | {_cell(_pair_label(unit.executor))} "
        f"| {_cell(_pair_label(unit.reviewer))} | {_cell(unit.verdict or 'none')} "
        f"| {'yes' if unit.parked else 'no'} | {unit.amendments} "
        f"| {'yes' if unit.delivered else 'no'} "
        f"| {_files_changed_cell(unit.workspace_files_changed)} "
        f"| {_cell(', '.join(unit.terminations) or 'not recorded')} |"
        for cell, unit in _merges_of(report)
    )
    return rows


def _without_derived(value: JsonValue, model: type[BaseModel]) -> JsonValue:
    """Drop from *value* the keys *model* derives rather than stores.

    The report is written with ``model_dump_json``, which serialises every
    ``computed_field``, and read back into models that are ``extra="forbid"``,
    which refuses them: the writer and the reader disagreed about the same
    file, so nothing this harness emitted could be loaded at all. Stripping is
    the honest direction, because a derived value read from a file is a second
    answer to something the model already decides.

    Derived from ``model_computed_fields`` rather than a list of names, since a
    list is one ``@computed_field`` away from disagreeing with the model.

    Args:
        value: The decoded JSON at this level.
        model: The model it will be validated against.

    Returns:
        The value, with this level's derived keys and its children's removed.
    """
    if not isinstance(value, dict):
        return value
    nested = {
        name: field.annotation
        for name, field in model.model_fields.items()
        if field.annotation is not None
    }
    stripped: dict[str, JsonValue] = {}
    for key, item in value.items():
        if key in model.model_computed_fields:
            continue
        child = _nested_model(nested.get(key))
        stripped[key] = (
            [_without_derived(entry, child) for entry in item]
            if child is not None and isinstance(item, list)
            else _without_derived(item, child)
            if child is not None
            else item
        )
    return stripped


def _nested_model(annotation: object) -> type[BaseModel] | None:
    """The model a field holds, directly or inside a sequence.

    Args:
        annotation: The field's declared type.

    Returns:
        The model, or ``None`` when the field holds no model.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def load_report(path: Path) -> RecursionDepthReport:
    """Read a committed report back.

    Args:
        path: The report JSON.

    Returns:
        The parsed report.
    """
    decoded: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    return RecursionDepthReport.model_validate(
        _without_derived(decoded, RecursionDepthReport)
    )


__all__ = [
    "REPORT_CHART_NAME",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "load_report",
    "write_report",
]

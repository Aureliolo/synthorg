# module-kind: code
"""Write the report: JSON, Markdown and the chart.

Three files rather than one because they answer different questions. The JSON is
what a later analysis reads; the Markdown is what a person reads; the SVG is the
one thing that gets pasted somewhere else, which is why its caveats are drawn
into it rather than left in the prose beside it.
"""

import json
from collections import defaultdict
from pathlib import Path

from evals.recursion_depth.chart import render_chart
from evals.recursion_depth.journal import cell_key
from evals.recursion_depth.manifest import Arm, ModelPair
from evals.recursion_depth.models import (
    MERGE,
    CellRecord,
    DepthPoint,
    RecursionDepthReport,
    UnitRecord,
)
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_REPORT_EMITTED

logger = get_logger(__name__)


def write_report(report: RecursionDepthReport, out_dir: Path) -> tuple[Path, ...]:
    """Write the JSON, the Markdown and the chart.

    Args:
        report: The assembled report.
        out_dir: Directory the three artifacts are written to.

    Returns:
        The written paths, in the order JSON, Markdown, SVG.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "depth_curve.json"
    md_path = out_dir / "depth_curve.md"
    svg_path = out_dir / "chart.svg"
    json_path.write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline=""
    )
    md_path.write_text(_markdown(report), encoding="utf-8", newline="")
    svg_path.write_text(
        render_chart(
            points=report.by_achieved_depth,
            caption_lines=_caption(report),
            by_cap=report.by_depth_cap,
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
    return (
        f"Runs reaching each depth: {histogram or 'none recorded'}.",
        *report.caveats,
    )


def _markdown(report: RecursionDepthReport) -> str:
    """Render the human-readable report.

    Returns:
        The Markdown text.
    """
    provenance = report.provenance
    lines = [
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
        f"- Total spend: {report.total_cost:.4f} across {report.total_tokens} tokens",
        "",
        "## Survival by depth reached",
        "",
        "The primary curve. Binned on the depth each leaf actually sat at, not",
        "on the cap its run was allowed: sweeping the cap does not sweep depth.",
        "",
        *_curve_table(report.by_achieved_depth),
        "",
        "## Survival by depth cap",
        "",
        "The manipulated variable, for comparison with the histogram below.",
        "",
        *_curve_table(report.by_depth_cap),
        "",
        "## How deep the runs went",
        "",
        *_histogram_table(report),
        "",
        "## What each arm spent, and what it bought",
        "",
        *_gate_table(report),
        "",
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
    # Two population columns rather than one: "Contributing" is what the
    # fraction is over, "Runs" is what the spend is over, and on the
    # achieved-depth curve they differ. One column for both invited a
    # spend-per-run read that divided across two populations.
    header = (
        "| Depth | Arm | Surviving | Delivered | Fraction | Contributing "
        "| Runs | Sessions | Tokens | Spend |"
    )
    rows = [header, "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for point in points:
        fraction = point.fraction
        # An absence rather than a zero: nothing was delivered at this depth,
        # so there is no rate to report and printing 0.000 would say the merge
        # lost work that never existed.
        rendered = "n/a" if fraction is None else f"{fraction:.3f}"
        rows.append(
            f"| {point.depth} | {point.arm.value} | {point.surviving_claims} "
            f"| {point.delivered_claims} | {rendered} | {point.cells} "
            f"| {point.runs} | {point.attempts} | {point.tokens} "
            f"| {point.cost:.4f} |"
        )
    return rows


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
    cost = dict.fromkeys(Arm, 0.0)
    for cell, unit in _merges_of(report):
        merges[cell.arm] += 1
        parked[cell.arm] += int(unit.parked)
        amendments[cell.arm] += unit.amendments
        attempts[cell.arm] += unit.attempts
        tokens[cell.arm] += unit.tokens
        cost[cell.arm] += unit.cost
    rows = [
        (
            "| Arm | Merges | Sessions | Tokens | Spend | Parked escalations "
            "| Contract amendments |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    rows.extend(
        f"| {arm.value} | {merges[arm]} | {attempts[arm]} | {tokens[arm]} "
        f"| {cost[arm]:.4f} | {parked[arm]} | {amendments[arm]} |"
        for arm in Arm
    )
    return rows


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
            _pair_label(unit.executor),
            _pair_label(unit.reviewer),
        )
        counts[pairing] += 1
    rows = ["| Arm | Assembled by | Judged by | Merges |", "|---|---|---|---:|"]
    rows.extend(
        f"| {arm} | {executor} | {reviewer} | {count} |"
        for (arm, executor, reviewer), count in sorted(counts.items())
    )
    return rows


def _merge_table(report: RecursionDepthReport) -> list[str]:
    """Render one row per merge, both parties named.

    Returns:
        The table lines.
    """
    rows = [
        (
            "| Cell | Depth | Assembly | Assembled by | Judged by | Verdict "
            "| Parked | Amendments | Delivered |"
        ),
        "|---|---:|---|---|---|---|---|---:|---|",
    ]
    rows.extend(
        f"| {cell_key(cell.depth_cap, cell.arm, cell.repetition)} | {unit.depth} "
        f"| {unit.title} | {_pair_label(unit.executor)} "
        f"| {_pair_label(unit.reviewer)} | {unit.verdict or 'none'} "
        f"| {'yes' if unit.parked else 'no'} | {unit.amendments} "
        f"| {'yes' if unit.delivered else 'no'} |"
        for cell, unit in _merges_of(report)
    )
    return rows


def load_report(path: Path) -> RecursionDepthReport:
    """Read a committed report back.

    Args:
        path: The report JSON.

    Returns:
        The parsed report.
    """
    return RecursionDepthReport.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )


__all__ = ["load_report", "write_report"]

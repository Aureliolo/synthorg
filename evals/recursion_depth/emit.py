# module-kind: code
"""Write the report: JSON, Markdown and the chart.

Three files rather than one because they answer different questions. The JSON is
what a later analysis reads; the Markdown is what a person reads; the SVG is the
one thing that gets pasted somewhere else, which is why its caveats are drawn
into it rather than left in the prose beside it.
"""

import json
from pathlib import Path

from evals.recursion_depth.chart import render_chart
from evals.recursion_depth.manifest import Arm
from evals.recursion_depth.models import DepthPoint, RecursionDepthReport
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_REPORT_EMITTED

logger = get_logger(__name__)

#: What the harness measures under, stated wherever the number is.
SIZING_CAVEAT = (
    "Unit sizing is the planner's own: the size signal reads the declaration a "
    "planner made, so this measures gated recursion UNDER PLANNER-DECLARED "
    "SIZING and cannot separate 'recursion fails' from 'the planner sized "
    "badly'. Separating them needs an agent that has read the code deciding its "
    "own split, which no published system has."
)

#: What the held-out oracle buys, stated for the same reason.
ORACLE_CAVEAT = (
    "The oracle is held out: it never enters a workspace and is named in no "
    "brief, so a delivery cannot be built to it."
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
        f"- Total spend: {report.total_cost:.4f}",
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
        "## Escalations and amendments",
        "",
        *_gate_table(report),
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
    rows = [
        "| Depth | Arm | Surviving | Delivered | Fraction | Runs | Sessions | Spend |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for point in points:
        fraction = point.fraction
        # An absence rather than a zero: nothing was delivered at this depth,
        # so there is no rate to report and printing 0.000 would say the merge
        # lost work that never existed.
        rendered = "n/a" if fraction is None else f"{fraction:.3f}"
        rows.append(
            f"| {point.depth} | {point.arm.value} | {point.surviving_claims} "
            f"| {point.delivered_claims} | {rendered} | {point.cells} "
            f"| {point.attempts} | {point.cost:.4f} |"
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


def _gate_table(report: RecursionDepthReport) -> list[str]:
    """Render the parked escalations and contract amendments per arm.

    Returns:
        The table lines.
    """
    parked = dict.fromkeys(Arm, 0)
    amendments = dict.fromkeys(Arm, 0)
    merges = dict.fromkeys(Arm, 0)
    for cell in report.measured_cells:
        for unit in cell.units:
            if unit.kind != "merge":
                continue
            merges[cell.arm] += 1
            parked[cell.arm] += int(unit.parked)
            amendments[cell.arm] += unit.amendments
    rows = [
        "| Arm | Merges | Parked escalations | Contract amendments |",
        "|---|---:|---:|---:|",
    ]
    rows.extend(
        f"| {arm.value} | {merges[arm]} | {parked[arm]} | {amendments[arm]} |"
        for arm in Arm
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


__all__ = ["ORACLE_CAVEAT", "SIZING_CAVEAT", "load_report", "write_report"]

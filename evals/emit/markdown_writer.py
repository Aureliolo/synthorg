"""Render a :class:`Scorecard` to deterministic Markdown.

The Markdown report is generated from the Scorecard alone (no side
data). Same Scorecard in, same Markdown bytes out: the rendering is a
pure function of the model so consumers can diff scorecard outputs
across runs without rendering churn.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from evals.models.scorecard import (
        AggregatedProcessFacts,
        BriefResult,
        JudgeCalibrationReport,
        Scorecard,
    )

logger = get_logger(__name__)

SCORECARD_MD_FILENAME: str = "scorecard.md"

# Column widths for the per-brief table. Tuned so a 32-char brief_id +
# the longest BriefKind label ("executable") fit without truncation.
COL_BRIEF: int = 36
COL_KIND: int = 10
COL_SCORE: int = 6


def _format_pct(num: int, denom: int) -> str:
    if denom == 0:
        return "n/a"
    return f"{num * 100 // denom}%"


def _render_brief_row(brief: BriefResult) -> str:
    return (
        f"| {brief.brief_id:<{COL_BRIEF}} "
        f"| {brief.kind.value:<{COL_KIND}} "
        f"| {brief.grade:>{COL_SCORE}} "
        f"| {brief.deduction:>{COL_SCORE}} "
        f"| {brief.score:>{COL_SCORE}} "
        f"| {brief.termination_reason} |"
    )


def _render_briefs_section(briefs: tuple[BriefResult, ...]) -> str:
    header = (
        f"| {'Brief':<{COL_BRIEF}} "
        f"| {'Kind':<{COL_KIND}} "
        f"| {'Grade':>{COL_SCORE}} "
        f"| {'Ded.':>{COL_SCORE}} "
        f"| {'Score':>{COL_SCORE}} "
        f"| Termination |"
    )
    sep = (
        f"|{'-' * (COL_BRIEF + 2)}"
        f"|{'-' * (COL_KIND + 2)}"
        f"|{'-' * (COL_SCORE + 2)}:"
        f"|{'-' * (COL_SCORE + 2)}:"
        f"|{'-' * (COL_SCORE + 2)}:"
        f"|-------------|"
    )
    rows = "\n".join(_render_brief_row(b) for b in briefs)
    return f"## Briefs\n\n{header}\n{sep}\n{rows}\n"


def _render_process_facts_section(facts: AggregatedProcessFacts) -> str:
    if facts.is_clean:
        return "## Process-fact penalties\n\nNo penalties applied.\n"
    rows = "\n".join(
        f"- `{event}`: {count}"
        for event, count in sorted(facts.events_by_class.items())
    )
    return f"## Process-fact penalties\n\n{rows}\n"


def _render_calibration_section(
    calibrations: tuple[JudgeCalibrationReport, ...],
) -> str:
    if not calibrations:
        return ""
    header = (
        "## Judge calibration\n\n"
        "| Rubric | Spearman | Gate | Pass? | Anchors |\n"
        "|---|---:|---:|:-:|---:|"
    )
    rows = "\n".join(
        f"| {c.rubric_id} | {c.spearman_rho:.3f} | {c.gate:.3f} "
        f"| {'yes' if c.passed else 'no'} | {c.anchor_count} |"
        for c in calibrations
    )
    return f"{header}\n{rows}\n"


def render_scorecard_md(scorecard: Scorecard) -> str:
    """Return the Markdown rendering of *scorecard*.

    The function is pure: no I/O, no logging, no clock reads. Callers
    that want to write to disk use :func:`write_scorecard_md`.
    """
    total_pct = _format_pct(scorecard.total, scorecard.max_total)
    generated = scorecard.generated_at.isoformat()
    header = (
        f"# SynthOrg benchmark scorecard\n\n"
        f"Total: **{scorecard.total} / {scorecard.max_total}** ({total_pct})  "
        f"-- schema v{scorecard.schema_version}  "
        f"-- {'PASS' if scorecard.is_passing else 'FAIL'}\n\n"
        f"Generated: {generated}\n"
        f"Company config: `{scorecard.company_config_path}`\n"
        f"Cassette: `{scorecard.cassette_path}`  "
        f"(sha256 `{scorecard.cassette_sha256[:16]}...`)\n"
        f"Suite version: `{scorecard.suite_version}`\n\n"
    )
    sections = [
        _render_briefs_section(scorecard.briefs),
        _render_process_facts_section(scorecard.process_facts),
        _render_calibration_section(scorecard.judge_calibrations),
    ]
    body = "\n".join(s for s in sections if s)
    return header + body


def write_scorecard_md(scorecard: Scorecard, out_dir: Path) -> Path:
    """Write the Markdown rendering of *scorecard* into *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / SCORECARD_MD_FILENAME
    target.write_text(render_scorecard_md(scorecard), encoding="utf-8")
    return target


__all__ = [
    "SCORECARD_MD_FILENAME",
    "render_scorecard_md",
    "write_scorecard_md",
]

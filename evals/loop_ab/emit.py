# module-kind: code
"""Write the A/B scoreboard to disk as JSON plus a readable Markdown table.

The JSON is the wire contract: schema-versioned and round-tripping through
``model_validate_json`` so it can be regression-tested and consumed later. The
Markdown is what a human actually reads to make the promotion call, so it leads
with the ranking, shows the real per-provider spend, and ends with the exact
settings values to paste.

Both land atomically via a same-directory tempfile and :meth:`Path.replace`,
matching :mod:`evals.emit.json_writer`: ``write_text`` truncates on open, so a
crash mid-write would otherwise leave a partial artifact that still parses as a
scoreboard.
"""

import contextlib
import os
import tempfile
from pathlib import Path

from evals.loop_ab.models import Scoreboard
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_LOOP_AB_SCOREBOARD_EMITTED

logger = get_logger(__name__)

SCOREBOARD_JSON_FILENAME: str = "scoreboard.json"
SCOREBOARD_MD_FILENAME: str = "scoreboard.md"
JSON_INDENT: int = 2


def _write_atomic(payload: str, target: Path) -> Path:
    """Write *payload* to *target* atomically.

    Returns:
        The written path.
    """
    out_dir = target.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=out_dir,
        encoding="utf-8",
        newline="\n",
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    try:
        temp_path.replace(target)
    except OSError:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise
    return target


def _provenance_lines(scoreboard: Scoreboard) -> list[str]:
    """Render the provenance header.

    Returns:
        Markdown lines describing what the scoreboard measured.
    """
    provenance = scoreboard.provenance
    weights = scoreboard.weights
    dirty = " (dirty tree)" if provenance.git_dirty else ""
    return [
        "# Inner execution-loop A/B scoreboard",
        "",
        f"- Measured against commit `{provenance.git_commit}`{dirty}",
        f"- Generated {provenance.generated_at.isoformat()}",
        f"- Brief suite `{provenance.brief_suite_version}`",
        f"- Manifest `{provenance.manifest_sha256}`",
        (
            f"- Rubric weights: correctness {weights.correctness}, tokens "
            f"{weights.tokens}, latency {weights.latency}, turns {weights.turns}, "
            f"resilience {weights.resilience} (gate floor "
            f"{weights.correctness_gate_floor:g})"
        ),
        f"- Total measured spend: {scoreboard.total_cost:.4f}",
        "",
    ]


def _results_table(scoreboard: Scoreboard) -> list[str]:
    """Render the measured rows, best composite first.

    Returns:
        Markdown lines for the results table.
    """
    lines = [
        "## Results",
        "",
        (
            "| Brief | Tier | Loop | Score | Correctness | Tokens "
            "| Wall-clock | Turns | Rework | Pass rate | Spend |"
        ),
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = sorted(
        scoreboard.measured_rows,
        key=lambda row: (
            row.brief_id,
            row.tier,
            -(row.score.composite if row.score else 0.0),
        ),
    )
    for row in rows:
        # Both are non-None on a measured row; the model validator guarantees it.
        score = row.score
        measurement = row.measurement
        if score is None or measurement is None:
            continue
        aggregate = measurement.aggregate
        spend = sum(item.cost for item in row.spend)
        flag = " (disqualified)" if score.disqualified else ""
        lines.append(
            f"| {row.brief_id} | {row.tier} | {row.loop_type}{flag} "
            f"| {score.composite:.1f} | {aggregate.correctness:.0f} "
            f"| {aggregate.total_tokens:.0f} | {aggregate.duration_seconds:.1f}s "
            f"| {aggregate.total_turns:.0f} | {aggregate.rework_events:.0f} "
            f"| {aggregate.pass_rate:.0%} | {spend:.4f} |"
        )
    lines.append("")
    return lines


def _spend_table(scoreboard: Scoreboard) -> list[str]:
    """Render spend broken down per ``(provider, model)``.

    Returns:
        Markdown lines for the spend breakdown, or an empty list when nothing
        was recorded.
    """
    totals: dict[tuple[str, str, str], tuple[int, int, float]] = {}
    for row in scoreboard.rows:
        for item in row.spend:
            key = (item.provider, item.model_id, item.currency)
            seen_in, seen_out, seen_cost = totals.get(key, (0, 0, 0.0))
            totals[key] = (
                seen_in + item.input_tokens,
                seen_out + item.output_tokens,
                seen_cost + item.cost,
            )
    if not totals:
        return []
    lines = [
        "## Spend by provider and model",
        "",
        "| Provider | Model | Input tokens | Output tokens | Cost | Currency |",
        "|---|---|---:|---:|---:|---|",
    ]
    for (provider, model_id, currency), (
        input_tokens,
        output_tokens,
        cost,
    ) in sorted(totals.items()):
        lines.append(
            f"| {provider} | {model_id} | {input_tokens} | {output_tokens} "
            f"| {cost:.4f} | {currency} |"
        )
    lines.append("")
    return lines


def _unavailable_section(scoreboard: Scoreboard) -> list[str]:
    """Render loops that could not be measured.

    Returns:
        Markdown lines naming each unavailable loop and why, or an empty list.
    """
    rows = scoreboard.unavailable_rows
    if not rows:
        return []
    lines = ["## Not measured", ""]
    lines.extend(
        f"- `{row.loop_type}` on `{row.brief_id}` ({row.tier}): "
        f"{row.unavailable_reason}"
        for row in rows
    )
    lines.append("")
    return lines


def _recommendation_section(scoreboard: Scoreboard) -> list[str]:
    """Render the promotion recommendation and its evidence.

    Returns:
        Markdown lines for the recommendation section.
    """
    recommendation = scoreboard.recommendation
    lines = ["## Promotion recommendation", ""]
    if recommendation.default_loop_type is None:
        lines.extend(
            [
                "No loop cleared the correctness gate, so this scoreboard "
                "supports no promotion. Leave the current settings in place.",
                "",
            ]
        )
        return lines
    lines.extend(
        [
            "Apply to the existing settings (no new selection machinery):",
            "",
            # Fenced with a language so the emitted scoreboard passes the same
            # markdown lint the hand-written design pages do.
            "```ini",
            f"engine.default_loop_type = {recommendation.default_loop_type}",
            "engine.loop_complexity_overrides = "
            f"{recommendation.loop_complexity_overrides}",
            "```",
            "",
            "Evidence, per complexity bucket:",
            "",
            "| Complexity | Winning loop | Composite |",
            "|---|---|---:|",
        ]
    )
    lines.extend(
        f"| {winner.complexity.value} | {winner.loop_type} | {winner.composite:.1f} |"
        for winner in recommendation.winners
    )
    lines.append("")
    return lines


def render_scoreboard_md(scoreboard: Scoreboard) -> str:
    """Render the whole scoreboard as Markdown.

    Returns:
        The rendered document.
    """
    lines = _provenance_lines(scoreboard)
    lines.extend(_results_table(scoreboard))
    lines.extend(_spend_table(scoreboard))
    lines.extend(_unavailable_section(scoreboard))
    lines.extend(_recommendation_section(scoreboard))
    return "\n".join(lines).rstrip("\n") + "\n"


def write_scoreboard(scoreboard: Scoreboard, out_dir: Path) -> tuple[Path, Path]:
    """Write the scoreboard JSON and Markdown into *out_dir*.

    Returns:
        ``(json_path, markdown_path)``.
    """
    json_path = _write_atomic(
        scoreboard.model_dump_json(indent=JSON_INDENT) + "\n",
        out_dir / SCOREBOARD_JSON_FILENAME,
    )
    md_path = _write_atomic(
        render_scoreboard_md(scoreboard), out_dir / SCOREBOARD_MD_FILENAME
    )
    logger.info(
        EVALS_LOOP_AB_SCOREBOARD_EMITTED,
        rows=len(scoreboard.rows),
        measured=len(scoreboard.measured_rows),
        unavailable=len(scoreboard.unavailable_rows),
        git_commit=scoreboard.provenance.git_commit,
    )
    return json_path, md_path


__all__ = [
    "SCOREBOARD_JSON_FILENAME",
    "SCOREBOARD_MD_FILENAME",
    "render_scoreboard_md",
    "write_scoreboard",
]

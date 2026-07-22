# module-kind: tests
"""The committed scoreboard artifact: schema, provenance and rendering.

Reproducibility is an acceptance criterion for this harness, not a nicety, so
the properties pinned here are: the artifact round-trips, it names the commit it
measured, an unmeasurable loop is reported rather than dropped, and the emitted
promotion values are the ones a reader is told to paste.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from evals.errors import ProvenanceUnavailableError
from evals.loop_ab.aggregate import LoopRepetitionSummary, Spread
from evals.loop_ab.emit import (
    SCOREBOARD_JSON_FILENAME,
    SCOREBOARD_MD_FILENAME,
    render_scoreboard_md,
    write_scoreboard,
)
from evals.loop_ab.models import (
    LoopBriefRow,
    Provenance,
    ProviderSpend,
    RubricWeights,
    Scoreboard,
)
from evals.loop_ab.promotion import ComplexityWinner, PromotionRecommendation
from evals.loop_ab.provenance import capture_provenance, manifest_digest
from evals.loop_ab.rubric import (
    RUBRIC_TOTAL,
    DimensionScores,
    LoopAggregate,
    LoopCellScore,
)
from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit


def _provenance() -> Provenance:
    """A fully-stamped provenance block."""
    return Provenance(
        generated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        git_commit=NotBlankStr("a" * 40),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:deadbeef"),
        brief_suite_version=NotBlankStr("sha256:cafe"),
    )


def _measurement(loop_type: str) -> LoopRepetitionSummary:
    """A reduced measurement for one loop."""
    return LoopRepetitionSummary(
        aggregate=LoopAggregate(
            loop_type=NotBlankStr(loop_type),
            correctness=100.0,
            total_tokens=1_000.0,
            duration_seconds=10.0,
            total_turns=5.0,
            rework_events=0.0,
            pass_rate=1.0,
        ),
        correctness_spread=Spread(minimum=100.0, median=100.0, maximum=100.0),
        repetitions=3,
    )


def _score(loop_type: str, *, composite: float = 90.0) -> LoopCellScore:
    """A scored row for one loop."""
    return LoopCellScore(
        loop_type=NotBlankStr(loop_type),
        dimensions=DimensionScores(
            correctness=1.0, tokens=1.0, latency=1.0, turns=1.0, resilience=1.0
        ),
        composite=composite,
        disqualified=False,
    )


def _measured_row(loop_type: str = "react") -> LoopBriefRow:
    """A row carrying a real measurement and its spend."""
    return LoopBriefRow(
        loop_type=NotBlankStr(loop_type),
        brief_id=NotBlankStr("loop-ab-simple"),
        tier=NotBlankStr("large"),
        model_id=NotBlankStr("example-large-001"),
        score=_score(loop_type),
        measurement=_measurement(loop_type),
        spend=(
            ProviderSpend(
                provider=NotBlankStr("example-provider"),
                model_id=NotBlankStr("example-large-001"),
                input_tokens=800,
                output_tokens=200,
                cost=0.25,
                currency=NotBlankStr("USD"),
            ),
        ),
    )


def _unavailable_row(reason: str = "sandbox image is not built") -> LoopBriefRow:
    """A row for a loop that could not be measured at all."""
    return LoopBriefRow(
        loop_type=NotBlankStr("openhands"),
        brief_id=NotBlankStr("loop-ab-simple"),
        tier=NotBlankStr("large"),
        model_id=NotBlankStr("example-large-001"),
        unavailable_reason=reason,
    )


def _scoreboard(*rows: LoopBriefRow) -> Scoreboard:
    """Assemble a scoreboard around *rows*."""
    return Scoreboard(
        provenance=_provenance(),
        weights=RubricWeights.current(),
        rows=rows or (_measured_row(),),
        recommendation=PromotionRecommendation(
            default_loop_type="react",
            loop_complexity_overrides="complex:openhands",
            winners=(
                ComplexityWinner(
                    complexity=Complexity.SIMPLE,
                    loop_type=NotBlankStr("react"),
                    composite=90.0,
                ),
            ),
        ),
    )


def test_the_scoreboard_round_trips_through_json() -> None:
    """The JSON is a wire contract, so it must reparse into an equal model."""
    scoreboard = _scoreboard()

    reparsed = Scoreboard.model_validate_json(scoreboard.model_dump_json())

    assert reparsed == scoreboard


def test_a_row_must_be_measured_or_unavailable_but_not_both() -> None:
    """The XOR is what stops an unwired loop being silently dropped."""
    with pytest.raises(ValueError, match="either measured"):
        LoopBriefRow(
            loop_type=NotBlankStr("openhands"),
            brief_id=NotBlankStr("loop-ab-simple"),
            tier=NotBlankStr("large"),
            model_id=NotBlankStr("example-large-001"),
            score=_score("openhands"),
            measurement=_measurement("openhands"),
            unavailable_reason="also unavailable",
        )


def test_a_row_that_is_neither_measured_nor_explained_is_refused() -> None:
    """An empty row would read as a measurement of nothing."""
    with pytest.raises(ValueError, match="either measured"):
        LoopBriefRow(
            loop_type=NotBlankStr("openhands"),
            brief_id=NotBlankStr("loop-ab-simple"),
            tier=NotBlankStr("large"),
            model_id=NotBlankStr("example-large-001"),
        )


def test_naive_timestamps_are_refused() -> None:
    """Artifacts must order unambiguously across machines."""
    with pytest.raises(ValueError, match="timezone-aware"):
        Provenance(
            generated_at=datetime(2026, 7, 22, 12, 0),  # noqa: DTZ001 -- the point
            git_commit=NotBlankStr("a" * 40),
            git_dirty=False,
            manifest_sha256=NotBlankStr("sha256:deadbeef"),
            brief_suite_version=NotBlankStr("sha256:cafe"),
        )


def test_the_stamped_weights_match_the_rubric_in_force() -> None:
    """A scoreboard must be readable without guessing which rubric produced it."""
    weights = RubricWeights.current()

    assert (
        weights.correctness
        + weights.tokens
        + weights.latency
        + weights.turns
        + weights.resilience
    ) == RUBRIC_TOTAL


def test_total_cost_sums_every_row_and_provider() -> None:
    """The headline spend figure is the real total, not one row's."""
    scoreboard = _scoreboard(_measured_row("react"), _measured_row("hybrid"))

    assert scoreboard.total_cost == pytest.approx(0.50)


def test_the_rendered_report_names_the_commit_it_measured() -> None:
    """A stale scoreboard must be self-evident to whoever reads it."""
    rendered = render_scoreboard_md(_scoreboard())

    assert "a" * 40 in rendered


def test_a_dirty_tree_is_disclosed_in_the_report() -> None:
    """The commit alone does not describe a tree with uncommitted changes."""
    scoreboard = _scoreboard().model_copy(
        update={"provenance": _provenance().model_copy(update={"git_dirty": True})}
    )

    assert "dirty tree" in render_scoreboard_md(scoreboard)


def test_an_unmeasured_loop_is_reported_with_its_reason() -> None:
    """Dropping the row would make a three-loop table look like a four-loop one."""
    scoreboard = _scoreboard(_measured_row(), _unavailable_row())

    rendered = render_scoreboard_md(scoreboard)

    assert "Not measured" in rendered
    assert "openhands" in rendered
    assert "sandbox image is not built" in rendered


def test_the_report_shows_spend_per_provider_and_model() -> None:
    """An organisation on several providers needs the breakdown, not a blend."""
    rendered = render_scoreboard_md(_scoreboard())

    assert "Spend by provider and model" in rendered
    assert "example-provider" in rendered
    assert "example-large-001" in rendered


def test_the_report_ends_with_pasteable_settings_values() -> None:
    """The recommendation is the artifact's whole point, so it must be explicit."""
    rendered = render_scoreboard_md(_scoreboard())

    assert "engine.default_loop_type = react" in rendered
    assert "engine.loop_complexity_overrides = complex:openhands" in rendered


def test_a_scoreboard_supporting_no_promotion_says_so() -> None:
    """No loop cleared the gate is a real outcome, not an empty section."""
    scoreboard = _scoreboard().model_copy(
        update={
            "recommendation": PromotionRecommendation(
                default_loop_type=None, loop_complexity_overrides="", winners=()
            )
        }
    )

    assert "supports no promotion" in render_scoreboard_md(scoreboard)


def test_both_artifacts_are_written(tmp_path: Path) -> None:
    """JSON for machines, Markdown for the person making the call."""
    json_path, md_path = write_scoreboard(_scoreboard(), tmp_path)

    assert json_path.name == SCOREBOARD_JSON_FILENAME
    assert md_path.name == SCOREBOARD_MD_FILENAME
    assert Scoreboard.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert md_path.read_text(encoding="utf-8").startswith("# Inner execution-loop")


def test_rewriting_leaves_no_temporary_files(tmp_path: Path) -> None:
    """The atomic write must not litter the artifact directory."""
    write_scoreboard(_scoreboard(), tmp_path)
    write_scoreboard(_scoreboard(), tmp_path)

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        SCOREBOARD_JSON_FILENAME,
        SCOREBOARD_MD_FILENAME,
    ]


def test_provenance_reads_the_real_repository(tmp_path: Path) -> None:
    """Provenance is captured from git itself, never hand-supplied."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("loops: []\n", encoding="utf-8")

    provenance = capture_provenance(
        repo_root=Path(__file__).resolve().parents[3],
        manifest_path=manifest,
        brief_suite_version="sha256:cafe",
    )

    assert len(provenance.git_commit) == 40
    assert provenance.manifest_sha256 == manifest_digest(manifest)


def test_provenance_outside_a_repository_fails_loud(tmp_path: Path) -> None:
    """An unnamed commit means an unreproducible scoreboard, so refuse it."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("loops: []\n", encoding="utf-8")

    with pytest.raises(ProvenanceUnavailableError):
        capture_provenance(
            repo_root=tmp_path,
            manifest_path=manifest,
            brief_suite_version="sha256:cafe",
        )

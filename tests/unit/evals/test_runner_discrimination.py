# module-kind: tests
"""Unit-tier scorer-discrimination check for the benchmark runner.

Mirrors the integration acceptance (``test_runner_broken_scores_worse``) at unit
tier so the discrimination logic is verifiable without booting under the
integration marker: a deliberately under-budgeted company config must score
measurably worse than the reference, with the gap attributable to a process-fact
penalty (not merely a lower grade).
"""

from pathlib import Path
from typing import Final

import pytest

from evals.errors import CassettePlaybackUnavailableError
from evals.models.brief import BriefKind
from evals.models.scorecard import BriefResult, Scorecard
from evals.run import run_benchmark_async
from evals.runner.deliverables import TEXT_NORMALISATION_BRIEF_ID
from evals.runner.profiles import BenchmarkStrategyProfile
from evals.scoring.executable import EXEC_TOTAL, EXEC_WEIGHT_HIDDEN

pytestmark = pytest.mark.unit

_EVALS: Final[Path] = Path(__file__).resolve().parents[3] / "evals"
_BRIEFS: Final[Path] = _EVALS / "briefs"
_ANCHORS: Final[Path] = _EVALS / "anchors"
# Broken must trail reference by at least this margin (matches the integration
# acceptance gate's SCORE_MARGIN).
_SCORE_MARGIN: Final[int] = 15


async def _run(
    company_yaml: str,
    out_dir: Path,
    *,
    profile: BenchmarkStrategyProfile = BenchmarkStrategyProfile.COMPETENT,
) -> Scorecard:
    return await run_benchmark_async(
        company_config=_EVALS / "baselines" / company_yaml,
        brief_suite=_BRIEFS,
        out_dir=out_dir,
        anchors_dir=_ANCHORS,
        strategy_profile=profile,
    )


def _executable_brief(scorecard: Scorecard) -> BriefResult:
    """Return the text-normalisation executable brief's result row."""
    rows = [b for b in scorecard.briefs if b.brief_id == TEXT_NORMALISATION_BRIEF_ID]
    assert len(rows) == 1, f"expected one executable brief row, got {len(rows)}"
    assert rows[0].kind is BriefKind.EXECUTABLE
    return rows[0]


async def test_cassette_without_provider_refuses_to_run(tmp_path: Path) -> None:
    """A cassette label without a replaying provider is a sharp failure.

    The cassette is descriptor-only; without an injected provider the run
    would mislabel the scorecard's determinism source. The guard fires before
    any work begins, so the cassette path need not even exist.
    """
    with pytest.raises(CassettePlaybackUnavailableError):
        await run_benchmark_async(
            company_config=_EVALS / "baselines" / "reference.yaml",
            brief_suite=_BRIEFS,
            out_dir=tmp_path / "out",
            anchors_dir=_ANCHORS,
            cassette=tmp_path / "recording.jsonl",
        )


async def test_broken_config_scores_measurably_worse(tmp_path: Path) -> None:
    """A starved company config scores below the reference by the margin."""
    reference = await _run("reference.yaml", tmp_path / "ref")
    broken = await _run("broken.yaml", tmp_path / "broken")

    assert reference.total >= broken.total + _SCORE_MARGIN, (
        f"broken={broken.total} reference={reference.total}"
    )


async def test_reference_is_process_clean_and_broken_is_not(tmp_path: Path) -> None:
    """Reference emits no process-fact penalties; broken emits at least one."""
    reference = await _run("reference.yaml", tmp_path / "ref")
    broken = await _run("broken.yaml", tmp_path / "broken")

    assert reference.process_facts.is_clean
    assert not broken.process_facts.is_clean


async def test_quality_delta_is_independent_of_the_budget_knob(tmp_path: Path) -> None:
    """A degraded deliverable scores worse on a clean budget: a pure quality gap.

    Runs the SAME (generously budgeted) reference company at the competent and
    degraded profiles. Neither run trips a budget penalty, so the executable
    brief's grade gap is attributable solely to its hidden test passing vs
    failing -- a grader-measured quality signal, not the broken-config budget
    knob the other discrimination tests exercise.
    """
    competent = await _run(
        "reference.yaml",
        tmp_path / "competent",
        profile=BenchmarkStrategyProfile.COMPETENT,
    )
    degraded = await _run(
        "reference.yaml",
        tmp_path / "degraded",
        profile=BenchmarkStrategyProfile.DEGRADED,
    )

    # Both runs are budget-clean: the gap cannot come from a process penalty.
    assert competent.process_facts.is_clean
    assert degraded.process_facts.is_clean
    assert competent.total > degraded.total

    competent_exec = _executable_brief(competent)
    degraded_exec = _executable_brief(degraded)
    # Competent passes every check; degraded compiles (build + lint pass) but
    # fails the hidden test, losing exactly the hidden-test weight.
    assert competent_exec.score == EXEC_TOTAL
    assert degraded_exec.score == EXEC_TOTAL - EXEC_WEIGHT_HIDDEN
    assert competent_exec.score - degraded_exec.score == EXEC_WEIGHT_HIDDEN


async def test_competent_executable_brief_passes_end_to_end(tmp_path: Path) -> None:
    """The executable brief is graded through the runner, not just in isolation.

    The competent deliverable is materialised into a work dir and the grader runs
    its hidden / build / lint commands against it, so the executable lane is
    exercised end to end (it had no end-to-end coverage before).
    """
    reference = await _run("reference.yaml", tmp_path / "ref")
    assert _executable_brief(reference).score == EXEC_TOTAL

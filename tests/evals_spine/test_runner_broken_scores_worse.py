"""Acceptance test for the golden-company benchmark.

A deliberately broken company config must score measurably worse than
the reference config under the same brief suite and deterministic
provider. This is the gating test for the whole eval spine. Marked
``@pytest.mark.integration`` because it boots a real SynthOrg agent
engine per brief and exercises the full run -> capture -> grade ->
scorecard path end-to-end.

The in-repo run uses the deterministic ``ScriptedDriver`` (free,
reproducible) rather than a recorded cassette; the broken config trails
the reference because its absurdly low per-run budget ceiling attributes
a budget-over process-fact penalty to every brief. The recorded-cassette
path stays available for operators who want an authentic scorecard.
"""

from pathlib import Path
from typing import Final

import pytest

from evals.models.scorecard import Scorecard
from evals.run import run_benchmark

pytestmark = pytest.mark.integration

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
EVALS: Final[Path] = REPO_ROOT / "evals"

# Broken config must trail reference by at least this many points for the
# scorer to demonstrate that it discriminates between healthy and broken
# company configs. Tuned to be comfortably above benign run-to-run noise
# while still leaving headroom for the scorer to evolve.
SCORE_MARGIN: Final[int] = 15


def _run(company_yaml: str, out_dir: Path) -> Scorecard:
    scorecard = run_benchmark(
        company_config=EVALS / "baselines" / company_yaml,
        brief_suite=EVALS / "briefs",
        out_dir=out_dir,
        anchors_dir=EVALS / "anchors",
    )
    assert isinstance(scorecard, Scorecard)
    return scorecard


def test_broken_company_scores_measurably_worse(tmp_path: Path) -> None:
    reference = _run("reference.yaml", tmp_path / "ref")
    broken = _run("broken.yaml", tmp_path / "broken")

    assert reference.total >= broken.total + SCORE_MARGIN, (
        f"broken={broken.total} reference={reference.total}; "
        "scorer is not discriminating between healthy and broken company configs"
    )


def test_reference_run_is_process_clean(tmp_path: Path) -> None:
    reference = _run("reference.yaml", tmp_path / "ref")
    assert reference.process_facts.is_clean, (
        "reference config emitted process-fact penalties; the reference run "
        "must be a clean baseline so the broken-vs-reference signal is unambiguous"
    )


def test_broken_run_has_process_fact_penalties(tmp_path: Path) -> None:
    broken = _run("broken.yaml", tmp_path / "broken")
    assert not broken.process_facts.is_clean, (
        "broken config emitted no process-fact penalties; the scorer must "
        "attribute at least part of the score gap to budget/loop/governance events"
    )


def test_scorecard_files_land_on_disk(tmp_path: Path) -> None:
    out_dir = tmp_path / "ref"
    _run("reference.yaml", out_dir)
    assert (out_dir / "scorecard.json").is_file()
    assert (out_dir / "scorecard.md").is_file()


def test_scorecard_json_round_trips_through_schema(tmp_path: Path) -> None:
    out_dir = tmp_path / "ref"
    written = _run("reference.yaml", out_dir)
    on_disk_text = (out_dir / "scorecard.json").read_text(encoding="utf-8")
    parsed = Scorecard.model_validate_json(on_disk_text)
    assert parsed == written

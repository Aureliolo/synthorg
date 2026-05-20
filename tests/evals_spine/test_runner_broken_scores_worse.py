"""Acceptance test for issue #1980: a deliberately broken company config
must score measurably worse than the reference config under the same
brief suite and cassette.

This is the gating test for the whole eval spine; written first (TDD).
"""

from pathlib import Path
from typing import Final

import pytest

from evals.models.scorecard import Scorecard
from evals.run import run_benchmark

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
EVALS: Final[Path] = REPO_ROOT / "evals"

# Broken config must trail reference by at least this many points for the
# scorer to demonstrate that it discriminates between healthy and broken
# company configs. Tuned to be comfortably above benign run-to-run noise
# while still leaving headroom for the scorer to evolve.
SCORE_MARGIN: Final[int] = 15


def _run(company_yaml: str, out_dir: Path) -> Scorecard:
    return run_benchmark(
        company_config=EVALS / "baselines" / company_yaml,
        brief_suite=EVALS / "briefs",
        cassette=EVALS / "cassettes" / "reference_run.cassette.json",
        out_dir=out_dir,
    )


@pytest.mark.integration
def test_broken_company_scores_measurably_worse(tmp_path: Path) -> None:
    reference = _run("reference.yaml", tmp_path / "ref")
    broken = _run("broken.yaml", tmp_path / "broken")

    assert reference.total > broken.total + SCORE_MARGIN, (
        f"broken={broken.total} reference={reference.total}; "
        "scorer is not discriminating between healthy and broken company configs"
    )


@pytest.mark.integration
def test_reference_run_is_process_clean(tmp_path: Path) -> None:
    reference = _run("reference.yaml", tmp_path / "ref")
    assert reference.process_facts.is_clean, (
        "reference config emitted process-fact penalties; the reference run "
        "must be a clean baseline so the broken-vs-reference signal is unambiguous"
    )


@pytest.mark.integration
def test_broken_run_has_process_fact_penalties(tmp_path: Path) -> None:
    broken = _run("broken.yaml", tmp_path / "broken")
    assert not broken.process_facts.is_clean, (
        "broken config emitted no process-fact penalties; the scorer must "
        "attribute at least part of the score gap to budget/loop/governance events"
    )


@pytest.mark.integration
def test_scorecard_files_land_on_disk(tmp_path: Path) -> None:
    out_dir = tmp_path / "ref"
    _run("reference.yaml", out_dir)
    assert (out_dir / "scorecard.json").is_file()
    assert (out_dir / "scorecard.md").is_file()


@pytest.mark.integration
def test_scorecard_json_round_trips_through_schema(tmp_path: Path) -> None:
    out_dir = tmp_path / "ref"
    written = _run("reference.yaml", out_dir)
    on_disk_text = (out_dir / "scorecard.json").read_text(encoding="utf-8")
    parsed = Scorecard.model_validate_json(on_disk_text)
    assert parsed == written

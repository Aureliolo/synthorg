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

from evals.models.scorecard import Scorecard
from evals.run import run_benchmark_async

pytestmark = pytest.mark.unit

_EVALS: Final[Path] = Path(__file__).resolve().parents[3] / "evals"
_BRIEFS: Final[Path] = _EVALS / "briefs"
_ANCHORS: Final[Path] = _EVALS / "anchors"
# Broken must trail reference by at least this margin (matches the integration
# acceptance gate's SCORE_MARGIN).
_SCORE_MARGIN: Final[int] = 15


async def _run(company_yaml: str, out_dir: Path) -> Scorecard:
    return await run_benchmark_async(
        company_config=_EVALS / "baselines" / company_yaml,
        brief_suite=_BRIEFS,
        out_dir=out_dir,
        anchors_dir=_ANCHORS,
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

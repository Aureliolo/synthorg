# module-kind: tests
"""Filesystem scorecard-history + learning-curve assembly tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from evals.history import ScorecardHistory
from evals.models.brief import BriefKind
from evals.models.scorecard import (
    AggregatedProcessFacts,
    BriefResult,
    JudgeCalibrationReport,
    ProcessFactReport,
    Scorecard,
)
from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.unit

_RUBRIC_ID: Final[str] = "default-bench"
_ANCHOR_COUNT: Final[int] = 5
_BASE: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)


def _scorecard(total: int, when: datetime, *, sha: str) -> Scorecard:
    calibration = JudgeCalibrationReport(
        rubric_id=NotBlankStr(_RUBRIC_ID),
        spearman_rho=1.0,
        gate=0.7,
        passed=True,
        anchor_count=_ANCHOR_COUNT,
    )
    brief_result = BriefResult(
        brief_id=NotBlankStr("checkout-resilience"),
        kind=BriefKind.JUDGED,
        grade=total,
        deduction=0,
        score=total,
        score_floor=0,
        process_facts=ProcessFactReport(),
        termination_reason=NotBlankStr("completed"),
        judge_calibration=calibration,
    )
    return Scorecard(
        generated_at=when,
        company_config_path=NotBlankStr("baselines/reference.yaml"),
        cassette_path=NotBlankStr("scripted:benchmark-provider"),
        cassette_sha256=NotBlankStr(sha),
        suite_version=NotBlankStr("sha256:abcdef0123456789"),
        briefs=(brief_result,),
        process_facts=AggregatedProcessFacts(),
        judge_calibrations=(calibration,),
    )


def _record(history: ScorecardHistory, totals: list[int]) -> None:
    for index, total in enumerate(totals):
        history.record(
            _scorecard(
                total,
                _BASE + timedelta(hours=index),
                sha=f"{index:064x}",
            )
        )


def test_curve_is_chronological_and_rising(tmp_path: Path) -> None:
    history = ScorecardHistory(tmp_path)
    _record(history, [20, 50, 80])

    curve = history.load_curve()

    assert [point.total for point in curve.points] == [20, 50, 80]
    assert [point.delta for point in curve.points] == [0, 30, 30]
    assert not curve.has_regression
    assert curve.latest_total == 80


def test_curve_flags_regression(tmp_path: Path) -> None:
    history = ScorecardHistory(tmp_path)
    _record(history, [20, 80, 10])

    curve = history.load_curve()

    assert curve.points[-1].is_regression
    assert curve.points[-1].delta == -70
    assert curve.has_regression
    # The earlier rising step is not a regression.
    assert not curve.points[1].is_regression


def test_empty_history_yields_empty_curve(tmp_path: Path) -> None:
    curve = ScorecardHistory(tmp_path / "absent").load_curve()
    assert curve.points == ()
    assert curve.latest_total is None
    assert not curve.has_regression

"""Unit tests for the eval-backed golden-scorecard provider seam."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.toolsmith.config import ToolsmithConfig, ToolValidationConfig
from synthorg.meta.toolsmith.golden_scorecard import EvalGoldenScorecardProvider
from synthorg.meta.toolsmith.models import ToolBlueprint
from synthorg.meta.toolsmith.validation_gate import BenchmarkToolValidationGate

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)


def _blueprint() -> ToolBlueprint:
    return ToolBlueprint(
        id="bp-1",
        name="synthorg_textkit_slugify",
        description="Slugify text.",
        capability="textkit:slugify",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        script_body="print('{}')",
        action_type="code:read",
        created_at=_NOW,
    )


class _RecordingRunner:
    """Records every ``run_scorecard`` call and replays scripted totals."""

    def __init__(self, *, baseline: int, candidate: int) -> None:
        self._baseline = baseline
        self._candidate = candidate
        self.calls: list[ToolBlueprint | None] = []

    async def run(self, blueprint: ToolBlueprint | None) -> int:
        self.calls.append(blueprint)
        return self._baseline if blueprint is None else self._candidate


class _FakeBrief:
    def __init__(self, *, passed: bool, score: int) -> None:
        self._passed = passed
        self._score = score

    async def run(self, blueprint: ToolBlueprint) -> tuple[bool, int]:
        del blueprint
        return self._passed, self._score


def _gate_config() -> ToolsmithConfig:
    return ToolsmithConfig(
        enabled=True,
        allowed_capabilities=(NotBlankStr("textkit:slugify"),),
        validation=ToolValidationConfig(require_golden_delta=True, min_score_margin=0),
    )


class TestEvalGoldenScorecardProvider:
    async def test_deterministic_arm_runs_once_and_returns_equal(self) -> None:
        # The default deterministic eval ignores the candidate tool, so the
        # provider runs the suite once and reports candidate == baseline: a
        # no-regression smoke check that always passes a 0-margin gate.
        runner = _RecordingRunner(baseline=250, candidate=999)
        provider = EvalGoldenScorecardProvider(run_scorecard=runner.run)

        baseline, candidate = await provider.score(_blueprint())

        assert (baseline, candidate) == (250, 250)
        assert runner.calls == [None]

    async def test_candidate_sensitive_runs_both_arms_in_order(self) -> None:
        runner = _RecordingRunner(baseline=250, candidate=240)
        provider = EvalGoldenScorecardProvider(
            run_scorecard=runner.run, candidate_sensitive=True
        )
        blueprint = _blueprint()

        baseline, candidate = await provider.score(blueprint)

        assert (baseline, candidate) == (250, 240)
        assert runner.calls == [None, blueprint]

    async def test_regressing_candidate_rejected_by_gate(self) -> None:
        runner = _RecordingRunner(baseline=250, candidate=240)
        gate = BenchmarkToolValidationGate(
            config=_gate_config(),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=EvalGoldenScorecardProvider(
                run_scorecard=runner.run, candidate_sensitive=True
            ),
        )

        result = await gate.validate(_blueprint())

        assert result.passed is False
        assert result.margin == -10

    async def test_neutral_candidate_registered_by_gate(self) -> None:
        runner = _RecordingRunner(baseline=250, candidate=250)
        gate = BenchmarkToolValidationGate(
            config=_gate_config(),
            brief_runner=_FakeBrief(passed=True, score=100),
            scorecard_provider=EvalGoldenScorecardProvider(
                run_scorecard=runner.run, candidate_sensitive=True
            ),
        )

        result = await gate.validate(_blueprint())

        assert result.passed is True
        assert result.margin == 0

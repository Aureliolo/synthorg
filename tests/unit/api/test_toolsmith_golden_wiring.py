"""Unit tests for the toolsmith golden-scorecard provider wiring seam."""

from datetime import UTC, datetime

import pytest

from synthorg.api.lifecycle_helpers.toolsmith_wiring import (
    _build_golden_scorecard_provider,
)
from synthorg.meta.toolsmith.errors import UnknownGoldenScorecardProviderError
from synthorg.meta.toolsmith.golden_scorecard import EvalGoldenScorecardProvider
from synthorg.meta.toolsmith.models import ToolBlueprint

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


class TestBuildGoldenScorecardProvider:
    def test_none_arm_wires_no_provider(self) -> None:
        assert _build_golden_scorecard_provider("none") is None

    def test_eval_arm_wires_eval_provider(self) -> None:
        provider = _build_golden_scorecard_provider("eval")
        assert isinstance(provider, EvalGoldenScorecardProvider)

    def test_unknown_arm_fails_loudly(self) -> None:
        with pytest.raises(UnknownGoldenScorecardProviderError):
            _build_golden_scorecard_provider("bogus")

    async def test_eval_provider_runs_the_real_golden_suite(self) -> None:
        # Proves the wired runner actually drives run_benchmark_async over
        # the reference golden company: paths resolve, kwargs are correct,
        # and Scorecard.total is extracted. The deterministic eval ignores
        # the candidate tool, so baseline == candidate (a no-regression
        # smoke check) and both totals are a positive suite score.
        provider = _build_golden_scorecard_provider("eval")
        assert isinstance(provider, EvalGoldenScorecardProvider)

        baseline, candidate = await provider.score(_blueprint())

        assert baseline == candidate
        assert baseline > 0

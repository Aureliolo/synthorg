"""Unit tests for the toolsmith golden-scorecard provider wiring seam."""

import pytest

from synthorg.api.lifecycle_helpers.toolsmith_wiring import (
    _build_golden_scorecard_provider,
)
from synthorg.meta.toolsmith.errors import UnknownGoldenScorecardProviderError
from synthorg.meta.toolsmith.golden_scorecard import EvalGoldenScorecardProvider

pytestmark = pytest.mark.unit


class TestBuildGoldenScorecardProvider:
    def test_none_arm_wires_no_provider(self) -> None:
        assert _build_golden_scorecard_provider("none") is None

    def test_eval_arm_wires_eval_provider(self) -> None:
        provider = _build_golden_scorecard_provider("eval")
        assert isinstance(provider, EvalGoldenScorecardProvider)

    def test_unknown_arm_fails_loudly(self) -> None:
        with pytest.raises(UnknownGoldenScorecardProviderError):
            _build_golden_scorecard_provider("bogus")

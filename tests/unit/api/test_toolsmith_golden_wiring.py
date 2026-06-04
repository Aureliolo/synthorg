"""Unit tests for the toolsmith golden-scorecard provider wiring seam."""

from pathlib import Path

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

    def test_eval_arm_wires_eval_provider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Stub the eval-harness locator so this unit only verifies arm
        # selection without depending on the repo-local ``evals/`` tree
        # (that end-to-end path is covered by the integration suite).
        monkeypatch.setattr(
            "synthorg.api.lifecycle_helpers.toolsmith_wiring._locate_evals_root",
            lambda: tmp_path,
        )
        provider = _build_golden_scorecard_provider("eval")
        assert isinstance(provider, EvalGoldenScorecardProvider)

    def test_unknown_arm_fails_loudly(self) -> None:
        with pytest.raises(UnknownGoldenScorecardProviderError):
            _build_golden_scorecard_provider("bogus")

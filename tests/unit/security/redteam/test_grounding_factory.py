"""Unit tests for the grounding-checker factory."""

import pytest
from typeguard import suppress_type_checks

from synthorg.security.redteam.grounding.factory import build_grounding_checker
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.protocol import GroundingChecker


@pytest.mark.unit
class TestBuildGroundingChecker:
    def test_heuristic_kind_returns_heuristic_implementation(self) -> None:
        checker = build_grounding_checker("heuristic")
        assert isinstance(checker, HeuristicGroundingChecker)
        assert isinstance(checker, GroundingChecker)

    def test_unknown_kind_raises(self) -> None:
        with (
            suppress_type_checks(),
            pytest.raises(ValueError, match="Unknown grounding checker kind"),
        ):
            build_grounding_checker("knowledge_substrate")  # type: ignore[arg-type]

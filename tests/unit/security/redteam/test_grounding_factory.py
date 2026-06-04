"""Unit tests for the grounding-checker factory."""

import pytest
from typeguard import suppress_type_checks

from synthorg.core.types import NotBlankStr
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.redteam.grounding.factory import build_grounding_checker
from synthorg.security.redteam.grounding.heuristic import HeuristicGroundingChecker
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.grounding.resolver import GroundingSubstrateContext
from synthorg.security.redteam.grounding.substrate import (
    KnowledgeSubstrateGroundingChecker,
)
from tests._shared.mock_of import mock_of


def _context() -> GroundingSubstrateContext:
    return GroundingSubstrateContext(
        knowledge_service=None,
        provider=mock_of[CompletionProvider](),
        model_id=NotBlankStr("example-medium-001"),
        cost_tracker=None,
    )


@pytest.mark.unit
class TestBuildGroundingChecker:
    def test_heuristic_kind_returns_heuristic_implementation(self) -> None:
        checker = build_grounding_checker("heuristic")
        assert isinstance(checker, HeuristicGroundingChecker)
        assert isinstance(checker, GroundingChecker)

    def test_substrate_kind_with_resolver_returns_substrate_checker(self) -> None:
        context = _context()
        checker = build_grounding_checker(
            "knowledge_substrate",
            substrate_resolver=lambda: context,
        )
        assert isinstance(checker, KnowledgeSubstrateGroundingChecker)
        assert isinstance(checker, GroundingChecker)

    def test_substrate_kind_without_resolver_degrades_to_heuristic(self) -> None:
        checker = build_grounding_checker("knowledge_substrate")
        assert isinstance(checker, HeuristicGroundingChecker)

    def test_heuristic_kind_ignores_a_supplied_resolver(self) -> None:
        context = _context()
        checker = build_grounding_checker(
            "heuristic",
            substrate_resolver=lambda: context,
        )
        assert isinstance(checker, HeuristicGroundingChecker)

    def test_unknown_kind_raises(self) -> None:
        with (
            suppress_type_checks(),
            pytest.raises(ValueError, match="Unknown grounding checker kind"),
        ):
            build_grounding_checker("made_up_kind")  # type: ignore[arg-type]

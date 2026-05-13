"""Tests for build_conflict_detector dispatch."""

import pytest

from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.conflict_detection import (
    AutoDetector,
    EmbeddingSimilarityDetector,
    HybridDetector,
    KeywordConflictDetector,
    LlmJudgeDetector,
    StructuredComparisonDetector,
)
from synthorg.communication.meeting.enums import ConflictDetectorType
from synthorg.communication.meeting.factory import build_conflict_detector
from synthorg.communication.meeting.protocol import ConflictDetector


@pytest.mark.unit
class TestBuildConflictDetector:
    """``build_conflict_detector`` returns the impl matching the enum."""

    @pytest.mark.parametrize(
        ("kind", "expected_cls"),
        [
            (ConflictDetectorType.KEYWORD, KeywordConflictDetector),
            (ConflictDetectorType.STRUCTURED, StructuredComparisonDetector),
            (ConflictDetectorType.LLM_JUDGE, LlmJudgeDetector),
            (ConflictDetectorType.EMBEDDING, EmbeddingSimilarityDetector),
            (ConflictDetectorType.HYBRID, HybridDetector),
            (ConflictDetectorType.AUTO, AutoDetector),
        ],
    )
    def test_returns_matching_impl(
        self,
        kind: ConflictDetectorType,
        expected_cls: type,
    ) -> None:
        config = StructuredPhasesConfig(conflict_detector=kind)
        detector = build_conflict_detector(config)
        assert isinstance(detector, expected_cls)

    @pytest.mark.parametrize(
        "kind",
        list(ConflictDetectorType),
    )
    def test_all_impls_satisfy_protocol(
        self,
        kind: ConflictDetectorType,
    ) -> None:
        """Every dispatched impl must satisfy the @runtime_checkable Protocol."""
        config = StructuredPhasesConfig(conflict_detector=kind)
        detector = build_conflict_detector(config)
        assert isinstance(detector, ConflictDetector)

    def test_default_config_picks_keyword(self) -> None:
        """The enum default routes to the Keyword detector."""
        detector = build_conflict_detector(StructuredPhasesConfig())
        assert isinstance(detector, KeywordConflictDetector)

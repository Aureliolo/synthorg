"""Tests for build_conflict_detector dispatch."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.conflict_detection import (
    AutoDetector,
    EmbeddingSimilarityDetector,
    HybridDetector,
    KeywordConflictDetector,
    LlmJudgeDetector,
    StructuredComparisonDetector,
)
from synthorg.communication.meeting.embedder import build_text_embedder
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


#: Two positions that share no substantive wording, so their hashed vectors
#: are near-orthogonal: above any plausible threshold they read as conflicting
#: and below zero they cannot.
_DIVERGENT_POSITIONS = (
    '{"positions": ['
    '{"recommendation": "ship the rewrite now"},'
    '{"proposal": "defer indefinitely, keep patching"}'
    "]}"
)


@pytest.mark.unit
class TestConflictSimilarityThreshold:
    """The configured threshold reaches the detector that scores with it."""

    @pytest.mark.parametrize(
        ("kind", "reader"),
        [
            (
                ConflictDetectorType.EMBEDDING,
                lambda d: d.similarity_threshold,
            ),
            (
                ConflictDetectorType.HYBRID,
                lambda d: d.embedding_detector.similarity_threshold,
            ),
        ],
    )
    def test_threshold_reaches_the_scoring_detector(
        self,
        kind: ConflictDetectorType,
        reader: Callable[[ConflictDetector], float],
    ) -> None:
        config = StructuredPhasesConfig(
            conflict_detector=kind,
            conflict_similarity_threshold=0.42,
        )
        assert reader(build_conflict_detector(config)) == 0.42

    @pytest.mark.parametrize(
        "kind",
        [ConflictDetectorType.EMBEDDING, ConflictDetectorType.HYBRID],
    )
    def test_threshold_changes_the_verdict(
        self,
        kind: ConflictDetectorType,
    ) -> None:
        """A value only reaches the behaviour if it can flip a verdict.

        Asserting the attribute alone would pass on a detector that stored
        the threshold and scored with the module constant.
        """
        sensitive = build_conflict_detector(
            StructuredPhasesConfig(
                conflict_detector=kind,
                conflict_similarity_threshold=1.0,
            )
        )
        blind = build_conflict_detector(
            StructuredPhasesConfig(
                conflict_detector=kind,
                conflict_similarity_threshold=0.0,
            )
        )
        assert sensitive.detect(_DIVERGENT_POSITIONS) is True
        assert blind.detect(_DIVERGENT_POSITIONS) is False

    @pytest.mark.parametrize("bad", [-0.01, 1.01])
    def test_threshold_is_bounded_to_the_cosine_range(self, bad: float) -> None:
        with pytest.raises(ValidationError):
            StructuredPhasesConfig(conflict_similarity_threshold=bad)

    def test_config_default_is_the_detector_default(self) -> None:
        """One constant backs both, so a directly-built detector agrees."""
        config = StructuredPhasesConfig(
            conflict_detector=ConflictDetectorType.EMBEDDING
        )
        detector = build_conflict_detector(config)
        assert isinstance(detector, EmbeddingSimilarityDetector)
        assert (
            detector.similarity_threshold
            == EmbeddingSimilarityDetector(
                embedder=build_text_embedder()
            ).similarity_threshold
        )

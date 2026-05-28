"""Meeting protocol factories.

Config-driven conflict-detector dispatch for the structured-phases
protocol, backed by :class:`StrategyRegistry`. The six concrete
detector classes live in
:mod:`synthorg.communication.meeting.conflict_detection`.
"""

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
from synthorg.communication.meeting.protocol import ConflictDetector
from synthorg.core.registry.strategy import StrategyRegistry

_CONFLICT_DETECTOR_REGISTRY: StrategyRegistry[ConflictDetector] = StrategyRegistry(
    {
        ConflictDetectorType.KEYWORD.value: KeywordConflictDetector,
        ConflictDetectorType.STRUCTURED.value: StructuredComparisonDetector,
        ConflictDetectorType.LLM_JUDGE.value: LlmJudgeDetector,
        ConflictDetectorType.EMBEDDING.value: EmbeddingSimilarityDetector,
        ConflictDetectorType.HYBRID.value: HybridDetector,
        ConflictDetectorType.AUTO.value: AutoDetector,
    },
    kind="conflict_detector",
)


def build_conflict_detector(config: StructuredPhasesConfig) -> ConflictDetector:
    """Construct a :class:`ConflictDetector` from ``config.conflict_detector``.

    Args:
        config: Structured-phases protocol configuration.

    Returns:
        The detector implementation selected by the discriminator.

    Raises:
        StrategyFactoryNotFoundError: ``config.conflict_detector`` is
            not a registered enum value.
    """
    return _CONFLICT_DETECTOR_REGISTRY.build(config.conflict_detector.value)

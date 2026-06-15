"""Meeting protocol factories.

Config-driven conflict-detector dispatch for the structured-phases
protocol, backed by :class:`StrategyRegistry`. The six concrete
detector classes live in
:mod:`synthorg.communication.meeting.conflict_detection`. The embedding
and hybrid detectors receive a :class:`TextEmbedder` selected by the
``embedder_strategy`` discriminator; the embedder is built lazily so a
keyword/structured detector never constructs a (possibly heavy) embedder.
"""

from collections.abc import Callable

from synthorg.communication.meeting.config import StructuredPhasesConfig
from synthorg.communication.meeting.conflict_detection import (
    AutoDetector,
    EmbeddingSimilarityDetector,
    HybridDetector,
    KeywordConflictDetector,
    LlmJudgeDetector,
    StructuredComparisonDetector,
)
from synthorg.communication.meeting.embedder import TextEmbedder, build_text_embedder
from synthorg.communication.meeting.enums import ConflictDetectorType
from synthorg.communication.meeting.protocol import ConflictDetector
from synthorg.core.registry.strategy import StrategyRegistry

_EmbedderFactory = Callable[[], TextEmbedder]


def _build_keyword(**_kwargs: object) -> ConflictDetector:
    return KeywordConflictDetector()


def _build_structured(**_kwargs: object) -> ConflictDetector:
    return StructuredComparisonDetector()


def _build_llm_judge(**_kwargs: object) -> ConflictDetector:
    return LlmJudgeDetector()


def _build_auto(**_kwargs: object) -> ConflictDetector:
    return AutoDetector()


def _build_embedding(
    *, embedder_factory: _EmbedderFactory, **_kwargs: object
) -> ConflictDetector:
    return EmbeddingSimilarityDetector(embedder=embedder_factory())


def _build_hybrid(
    *, embedder_factory: _EmbedderFactory, **_kwargs: object
) -> ConflictDetector:
    return HybridDetector(embedder=embedder_factory())


_CONFLICT_DETECTOR_REGISTRY: StrategyRegistry[ConflictDetector] = StrategyRegistry(
    {
        ConflictDetectorType.KEYWORD.value: _build_keyword,
        ConflictDetectorType.STRUCTURED.value: _build_structured,
        ConflictDetectorType.LLM_JUDGE.value: _build_llm_judge,
        ConflictDetectorType.EMBEDDING.value: _build_embedding,
        ConflictDetectorType.HYBRID.value: _build_hybrid,
        ConflictDetectorType.AUTO.value: _build_auto,
    },
    kind="conflict_detector",
)


def build_conflict_detector(config: StructuredPhasesConfig) -> ConflictDetector:
    """Construct a :class:`ConflictDetector` from ``config.conflict_detector``.

    The embedding / hybrid detectors are handed a lazy embedder factory
    bound to ``config.embedder_strategy`` so the embedder (which may load
    a model) is only built when one of those detectors is selected.

    Args:
        config: Structured-phases protocol configuration.

    Returns:
        The detector implementation selected by the discriminator.

    Raises:
        StrategyFactoryNotFoundError: ``config.conflict_detector`` is
            not a registered enum value.
    """
    return _CONFLICT_DETECTOR_REGISTRY.build(
        config.conflict_detector.value,
        embedder_factory=lambda: build_text_embedder(config.embedder_strategy),
    )

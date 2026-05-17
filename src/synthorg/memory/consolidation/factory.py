"""Consolidation strategy factory (ADR-0005 + RFC#2).

Maps a :class:`ConsolidationStrategyType` discriminator to a
``Composite(HighestRelevanceSelector, <op>)`` via a ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry` (the RFC#2
registry). Replaces hand-injecting a monolithic strategy class.

The op-specific dependencies (LLM provider/model, density
classifier/extractor/summarizer) are passed in a
:class:`ConsolidationDeps` bundle; each builder validates the deps it
needs and raises :class:`~synthorg.memory.errors.MemoryConfigError`
when one is missing, so a misconfigured composition fails fast at
construction.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.memory.consolidation.composite import (
    CompositeConsolidationStrategy,
)
from synthorg.memory.consolidation.config import (
    ConsolidationStrategyType,
    LLMConsolidationConfig,
)
from synthorg.memory.consolidation.llm_op import LLMSynthesisOp
from synthorg.memory.consolidation.ops import (
    ConcatenationOp,
    DensityRoutingOp,
)
from synthorg.memory.consolidation.selectors import HighestRelevanceSelector
from synthorg.memory.errors import MemoryConfigError

if TYPE_CHECKING:
    from synthorg.budget.tracker import CostTracker
    from synthorg.core.types import NotBlankStr
    from synthorg.memory.consolidation.abstractive import AbstractiveSummarizer
    from synthorg.memory.consolidation.density import DensityClassifier
    from synthorg.memory.consolidation.extractive import ExtractivePreserver
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.providers.protocol import CompletionProvider


@dataclass(frozen=True, slots=True)
class ConsolidationDeps:
    """Wired dependencies for building a consolidation composite.

    ``backend`` is always required. The remaining fields are required
    only for the strategy types that consume them (validated by the
    per-type builders).
    """

    backend: MemoryBackend
    group_threshold: int = 3
    provider: CompletionProvider | None = None
    model: NotBlankStr | None = None
    llm_config: LLMConsolidationConfig | None = None
    cost_tracker: CostTracker | None = None
    classifier: DensityClassifier | None = None
    extractor: ExtractivePreserver | None = None
    summarizer: AbstractiveSummarizer | None = None


def _require(value: object, name: str, strategy: str) -> object:
    """Return *value* or raise if it is ``None``."""
    if value is None:
        msg = (
            f"consolidation strategy {strategy!r} requires "
            f"{name!r} but it was not provided"
        )
        raise MemoryConfigError(msg)
    return value


def _build_simple(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=ConcatenationOp(backend=deps.backend),
    )


def _build_dual_mode(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    classifier = _require(deps.classifier, "classifier", "dual_mode")
    extractor = _require(deps.extractor, "extractor", "dual_mode")
    summarizer = _require(deps.summarizer, "summarizer", "dual_mode")
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=DensityRoutingOp(
            backend=deps.backend,
            classifier=classifier,  # type: ignore[arg-type]
            extractor=extractor,  # type: ignore[arg-type]
            summarizer=summarizer,  # type: ignore[arg-type]
        ),
    )


def _build_llm(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    provider = _require(deps.provider, "provider", "llm")
    model = _require(deps.model, "model", "llm")
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=LLMSynthesisOp(
            backend=deps.backend,
            provider=provider,  # type: ignore[arg-type]
            model=model,  # type: ignore[arg-type]
            config=deps.llm_config,
            cost_tracker=deps.cost_tracker,
        ),
        parallel=True,
    )


_CONSOLIDATION_REGISTRY: StrategyRegistry[CompositeConsolidationStrategy] = (
    StrategyRegistry(
        {
            ConsolidationStrategyType.SIMPLE: _build_simple,
            ConsolidationStrategyType.DUAL_MODE: _build_dual_mode,
            ConsolidationStrategyType.LLM: _build_llm,
        },
        kind="consolidation_strategy",
    )
)


def build_consolidation_strategy(
    strategy_type: ConsolidationStrategyType,
    deps: ConsolidationDeps,
) -> CompositeConsolidationStrategy:
    """Build the composite for *strategy_type* from *deps*.

    Args:
        strategy_type: Which composite to build.
        deps: Wired dependencies (op-specific ones validated per type).

    Returns:
        A ``CompositeConsolidationStrategy`` satisfying the existing
        ``ConsolidationStrategy`` Protocol.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``strategy_type``.
        MemoryConfigError: A required dependency is missing.
    """
    return _CONSOLIDATION_REGISTRY.build(strategy_type, deps)

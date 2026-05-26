"""Consolidation strategy factory.

Maps a :class:`ConsolidationStrategyType` discriminator to a
``Composite(HighestRelevanceSelector, <op>)`` via a ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`, so a composition is
selected by config rather than by hand-injecting a monolithic class.

The op-specific dependencies (LLM provider/model, density
classifier/extractor/summarizer) are passed in a
:class:`ConsolidationDeps` bundle; each builder validates the deps it
needs and raises :class:`~synthorg.memory.errors.MemoryConfigError`
when one is missing, so a misconfigured composition fails fast at
construction.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

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

_DEFAULT_GROUP_THRESHOLD: Final[int] = 3


@dataclass(frozen=True, slots=True)
class ConsolidationDeps:
    """Wired dependencies for building a consolidation composite.

    ``backend`` is always required. The remaining fields are required
    only for the strategy types that consume them (validated by the
    per-type builders).
    """

    backend: MemoryBackend
    group_threshold: int = _DEFAULT_GROUP_THRESHOLD
    provider: CompletionProvider | None = None
    model: NotBlankStr | None = None
    llm_config: LLMConsolidationConfig | None = None
    cost_tracker: CostTracker | None = None
    classifier: DensityClassifier | None = None
    extractor: ExtractivePreserver | None = None
    summarizer: AbstractiveSummarizer | None = None


def _require[T](value: T | None, name: str, strategy: str) -> T:
    """Return *value* or raise if it is ``None``.

    Returns:
        Result of type ``T``.

    Raises:
        MemoryConfigError: If the related operation fails.
    """
    if value is None:
        msg = (
            f"consolidation strategy {strategy!r} requires "
            f"{name!r} but it was not provided"
        )
        raise MemoryConfigError(msg)
    return value


def _build_simple(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    """Build the SIMPLE strategy: highest-relevance selection + concatenation.

    Returns:
        A composite strategy that concatenates the top-relevance group
        without LLM synthesis.
    """
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=ConcatenationOp(backend=deps.backend),
    )


def _build_dual_mode(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    """Build the DUAL_MODE strategy: density-routed extract-or-summarise.

    Returns:
        A composite strategy that routes each group to extraction or
        summarisation based on information density.
    """
    classifier = _require(deps.classifier, "classifier", "dual_mode")
    extractor = _require(deps.extractor, "extractor", "dual_mode")
    summarizer = _require(deps.summarizer, "summarizer", "dual_mode")
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=DensityRoutingOp(
            backend=deps.backend,
            classifier=classifier,
            extractor=extractor,
            summarizer=summarizer,
        ),
    )


def _build_llm(deps: ConsolidationDeps) -> CompositeConsolidationStrategy:
    """Build the LLM strategy: highest-relevance selection + LLM synthesis.

    Returns:
        A composite strategy that synthesises each selected group with
        the configured provider/model, running groups in parallel.
    """
    provider = _require(deps.provider, "provider", "llm")
    model = _require(deps.model, "model", "llm")
    return CompositeConsolidationStrategy(
        selector=HighestRelevanceSelector(group_threshold=deps.group_threshold),
        op=LLMSynthesisOp(
            backend=deps.backend,
            provider=provider,
            model=model,
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

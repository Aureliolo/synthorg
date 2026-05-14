"""Memory injection strategy factory.

Dispatches :class:`MemoryRetrievalConfig.strategy` (an
:class:`InjectionStrategy` enum value) to the matching
:class:`MemoryInjectionStrategy` implementation. The three concrete
strategies live in
:mod:`synthorg.memory.retriever` (``ContextInjectionStrategy``),
:mod:`synthorg.memory.tool_retriever` (``ToolBasedInjectionStrategy``),
and :mod:`synthorg.memory.self_editing` (``SelfEditingMemoryStrategy``).

Constructor shapes diverge across the three impls (e.g. only
``ContextInjectionStrategy`` consumes ``hierarchical_retriever`` and
``reranker``), so dispatch uses per-strategy builder closures rather
than passing a uniform deps tuple where most fields would be ignored.
"""

from typing import TYPE_CHECKING, assert_never

from synthorg.memory.injection import InjectionStrategy
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy

if TYPE_CHECKING:
    from synthorg.memory.filter import MemoryFilterStrategy
    from synthorg.memory.injection import MemoryInjectionStrategy, TokenEstimator
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.memory.reformulation import QueryReformulator, SufficiencyChecker
    from synthorg.memory.retrieval.protocol import HierarchicalRetriever
    from synthorg.memory.retrieval.reranking.protocol import QuerySpecificReranker
    from synthorg.memory.retrieval_config import MemoryRetrievalConfig
    from synthorg.memory.self_editing import SelfEditingMemoryConfig
    from synthorg.memory.shared import SharedKnowledgeStore


def build_memory_injection_strategy(  # noqa: PLR0913 -- per-strategy deps surface
    config: MemoryRetrievalConfig,
    *,
    backend: MemoryBackend,
    shared_store: SharedKnowledgeStore | None = None,
    token_estimator: TokenEstimator | None = None,
    memory_filter: MemoryFilterStrategy | None = None,
    hierarchical_retriever: HierarchicalRetriever | None = None,
    reranker: QuerySpecificReranker | None = None,
    reformulator: QueryReformulator | None = None,
    sufficiency_checker: SufficiencyChecker | None = None,
    self_editing_config: SelfEditingMemoryConfig | None = None,
) -> MemoryInjectionStrategy:
    """Build the strategy selected by ``config.strategy``.

    Per-strategy deps below are accepted for every dispatch path; deps
    that the selected strategy does not consume are silently ignored
    (e.g. passing ``reformulator`` while building ``CONTEXT`` is not a
    misconfiguration but also is not used). Each per-strategy entry in
    the Args list below names the strategies that actually consume it.

    Args:
        config: Retrieval pipeline configuration. The ``strategy``
            field is the dispatch discriminator.
        backend: Memory backend shared by all three strategies.
        shared_store: Optional shared knowledge store
            (context / tool-based only; ignored by self-editing).
        token_estimator: Token estimator for budget-fit
            (context / self-editing only; ignored by tool-based).
        memory_filter: Post-ranking filter (context-only).
        hierarchical_retriever: Hierarchical retriever (context-only).
        reranker: Query-specific reranker (context-only).
        reformulator: Query reformulator (tool-based only; required
            when ``config.query_reformulation_enabled`` is True).
        sufficiency_checker: Pairs with ``reformulator`` (tool-based).
        self_editing_config: Self-editing configuration (self-editing
            only).

    Returns:
        The concrete strategy matching ``config.strategy``.

    Raises:
        AssertionError: ``config.strategy`` is not one of the registered
            enum values. Raised by :func:`typing.assert_never`; mypy
            strict catches this at the type-check boundary, so this is
            a runtime guard for new variants added without updating
            the factory.
    """
    match config.strategy:
        case InjectionStrategy.CONTEXT:
            return ContextInjectionStrategy(
                backend=backend,
                config=config,
                shared_store=shared_store,
                token_estimator=token_estimator,
                memory_filter=memory_filter,
                hierarchical_retriever=hierarchical_retriever,
                reranker=reranker,
            )
        case InjectionStrategy.TOOL_BASED:
            return ToolBasedInjectionStrategy(
                backend=backend,
                config=config,
                shared_store=shared_store,
                reformulator=reformulator,
                sufficiency_checker=sufficiency_checker,
            )
        case InjectionStrategy.SELF_EDITING:
            return SelfEditingMemoryStrategy(
                backend=backend,
                config=self_editing_config,
                token_estimator=token_estimator,
            )
        case _:  # pragma: no cover
            assert_never(config.strategy)

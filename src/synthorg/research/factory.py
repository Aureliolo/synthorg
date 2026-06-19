"""Service factory for the research subsystem.

Assembles the pipeline strategies behind the :class:`ResearchConfig`
discriminators and returns a wired :class:`ResearchService`. Retrieval
sources are included only when their provider / service is injected,
mirroring the vendor-agnostic house pattern: a missing provider simply
means that source family does not fan out.
"""

from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock
from synthorg.knowledge.service import KnowledgeService
from synthorg.observability import get_logger
from synthorg.observability.events.research import RESEARCH_UNAVAILABLE
from synthorg.persistence.research_protocol import ResearchRunRepository
from synthorg.providers.protocol import CompletionProvider
from synthorg.research.config import ResearchConfig
from synthorg.research.enums import ResearchSourceType
from synthorg.research.errors import ResearchUnavailableError
from synthorg.research.planning.llm_planner import LlmQueryPlanner
from synthorg.research.retrieval.dedup import (
    Embedder,
    EmbeddingDeduplicator,
    LexicalDeduplicator,
)
from synthorg.research.retrieval.protocol import Deduplicator, RetrievalSource
from synthorg.research.retrieval.providers import (
    AcademicSearchProvider,
    CodeSearchProvider,
)
from synthorg.research.retrieval.sources.academic import AcademicRetrievalSource
from synthorg.research.retrieval.sources.code import CodeRetrievalSource
from synthorg.research.retrieval.sources.knowledge import KnowledgeRetrievalSource
from synthorg.research.retrieval.sources.web import WebRetrievalSource
from synthorg.research.service import ResearchService
from synthorg.research.synthesis.citation_binder import CitationBinder
from synthorg.research.synthesis.llm_synthesizer import LlmSynthesizer
from synthorg.research.triage.heuristic import HeuristicCredibilityTriage
from synthorg.research.triage.hybrid import HybridCredibilityTriage
from synthorg.research.triage.llm import LlmCredibilityTriage
from synthorg.research.triage.protocol import CredibilityTriage
from synthorg.tools.web.web_search import WebSearchProvider

logger = get_logger(__name__)


def _build_triage(
    config: ResearchConfig,
    *,
    provider: CompletionProvider,
    model: str,
    clock: Clock | None,
    cost_tracker: CostTracker | None,
) -> CredibilityTriage:
    """Select the credibility-triage strategy per the config discriminator.

    Returns:
        The heuristic, LLM, or hybrid triage strategy per
        ``config.credibility_triage``.
    """
    heuristic = HeuristicCredibilityTriage(clock=clock)
    if config.credibility_triage == "heuristic":
        return heuristic
    llm = LlmCredibilityTriage(
        provider=provider,
        model=model,
        batch_size=config.triage_batch_size,
        cost_tracker=cost_tracker,
    )
    if config.credibility_triage == "llm":
        return llm
    return HybridCredibilityTriage(
        heuristic=heuristic,
        llm=llm,
        prefilter_factor=config.hybrid_prefilter_factor,
    )


def _build_deduplicator(
    config: ResearchConfig,
    *,
    embedder: Embedder | None,
) -> Deduplicator:
    """Select the deduplication strategy per the config discriminator.

    Returns:
        The embedding deduplicator when configured (and an embedder is
        present), otherwise the lexical deduplicator.

    Raises:
        ResearchUnavailableError: When the embedding deduplicator is
            selected but no embedder is injected.
    """
    if config.deduplicator == "embedding":
        if embedder is None:
            msg = "embedding deduplicator requires an embedder to be injected"
            logger.warning(
                RESEARCH_UNAVAILABLE,
                reason="embedding_deduplicator_missing_embedder",
                error_type=ResearchUnavailableError.__name__,
            )
            raise ResearchUnavailableError(msg)
        return EmbeddingDeduplicator(
            embedder=embedder, threshold=config.dedup_similarity_threshold
        )
    return LexicalDeduplicator(threshold=config.dedup_similarity_threshold)


def _build_sources(
    *,
    knowledge_service: KnowledgeService | None,
    web_search_provider: WebSearchProvider | None,
    academic_provider: AcademicSearchProvider | None,
    code_provider: CodeSearchProvider | None,
    clock: Clock | None,
) -> dict[ResearchSourceType, RetrievalSource]:
    """Build the retrieval-source map from whatever providers are present.

    Returns:
        A map from ``ResearchSourceType`` to retrieval source for every
        provider that was injected.
    """
    sources: dict[ResearchSourceType, RetrievalSource] = {}
    if knowledge_service is not None:
        sources[ResearchSourceType.KNOWLEDGE] = KnowledgeRetrievalSource(
            service=knowledge_service
        )
    if web_search_provider is not None:
        sources[ResearchSourceType.WEB] = WebRetrievalSource(
            provider=web_search_provider, clock=clock
        )
    if academic_provider is not None:
        sources[ResearchSourceType.ACADEMIC] = AcademicRetrievalSource(
            provider=academic_provider
        )
    if code_provider is not None:
        sources[ResearchSourceType.CODE] = CodeRetrievalSource(provider=code_provider)
    return sources


def build_research_service(  # noqa: PLR0913 -- injected boot collaborators
    *,
    runs_repo: ResearchRunRepository,
    provider: CompletionProvider,
    model: str,
    config: ResearchConfig,
    knowledge_service: KnowledgeService | None = None,
    web_search_provider: WebSearchProvider | None = None,
    academic_provider: AcademicSearchProvider | None = None,
    code_provider: CodeSearchProvider | None = None,
    embedder: Embedder | None = None,
    clock: Clock | None = None,
    cost_tracker: CostTracker | None = None,
) -> ResearchService:
    """Wire a :class:`ResearchService` from the config + injected providers.

    Returns:
        A fully wired :class:`ResearchService`.

    Raises:
        ResearchUnavailableError: If research mode is disabled in *config*
            or a selected strategy lacks a required dependency.
    """
    if not config.enabled:
        msg = "research mode is disabled in config"
        logger.warning(
            RESEARCH_UNAVAILABLE,
            reason="research_mode_disabled",
            error_type=ResearchUnavailableError.__name__,
        )
        raise ResearchUnavailableError(msg)
    return ResearchService(
        planner=LlmQueryPlanner(
            provider=provider, model=model, cost_tracker=cost_tracker
        ),
        sources=_build_sources(
            knowledge_service=knowledge_service,
            web_search_provider=web_search_provider,
            academic_provider=academic_provider,
            code_provider=code_provider,
            clock=clock,
        ),
        triage=_build_triage(
            config,
            provider=provider,
            model=model,
            clock=clock,
            cost_tracker=cost_tracker,
        ),
        deduplicator=_build_deduplicator(config, embedder=embedder),
        synthesizer=LlmSynthesizer(
            provider=provider,
            model=model,
            binder=CitationBinder(),
            clock=clock,
            cost_tracker=cost_tracker,
        ),
        runs_repo=runs_repo,
        per_query_limit=config.per_query_limit,
        clock=clock,
    )

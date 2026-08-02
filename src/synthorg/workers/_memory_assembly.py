# module-kind: service
"""Memory collaborators the boot ``AgentEngine`` is assembled with.

Kept beside :mod:`synthorg.workers._engine_assembly` rather than inside
it so the engine-assembly module stays within its size budget, and so
the memory seam, which decides what an agent recalls before it acts, is
reviewable on its own.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import ValidationError

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.text_estimation import DefaultTokenEstimator
from synthorg.core.types import NotBlankStr
from synthorg.llm.model_pins import pin_for
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.injection_factory import build_memory_injection_strategy
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.reformulation import (
    LLMQueryReformulator,
    LLMSufficiencyChecker,
    QueryReformulator,
    SufficiencyChecker,
)
from synthorg.memory.retrieval.factory import create_hierarchical_retriever
from synthorg.memory.retrieval.protocol import HierarchicalRetriever
from synthorg.memory.retrieval.reranking.llm_reranker import LLMQuerySpecificReranker
from synthorg.memory.retrieval.reranking.protocol import QuerySpecificReranker
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.memory.shared_store import OrgSharedKnowledgeStore
from synthorg.memory.state import MemoryStateSlice, org_memory_backend_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.procedural_memory import (
    PROCEDURAL_MEMORY_CONFIG_RESOLVE_FAILED,
)
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.structured_text import complete_text
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


# The reformulation and sufficiency prompts are self-contained (they
# carry their own instructions and format), so the system message only
# has to keep the model on task and terse.
_RETRIEVAL_SYSTEM_PROMPT = (
    "You assist a memory-retrieval pipeline. Follow the instructions in the "
    "message exactly and reply with only what they ask for, nothing else."
)


def wiki_exporter_or_none(app_state: AppState) -> WikiExporter | None:
    """Build the wiki exporter backing ``memory.browse_wiki``.

    Returns:
        The exporter, or ``None`` when no memory backend is wired.
    """
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        return None
    return WikiExporter(
        backend=backend,
        config=app_state.config.memory.consolidation.wiki_export,
    )


def _retrieval_completion_fn(
    *,
    provider: CompletionProvider,
    model: NotBlankStr,
    cost_tracker: CostTrackerProtocol | None,
) -> Callable[[str], Awaitable[str]]:
    """Adapt an explicit ``(provider, model)`` pair to a completion callback.

    The reformulator and sufficiency checker consume a bare
    prompt-in/text-out callable; this binds the pinned model, purpose
    attribution and cost recording behind that shape so both dispatch on
    the engine's provider rather than picking a client themselves.

    Returns:
        An async callable taking a prompt and returning the response text.
    """

    async def _complete(prompt: str) -> str:
        content, _ = await complete_text(
            provider,
            model,
            system=_RETRIEVAL_SYSTEM_PROMPT,
            user=prompt,
            purpose=PromptPurposeId.MEMORY_RETRIEVAL_RETRY,
            cost_tracker=cost_tracker,
        )
        return content

    return _complete


def _build_reranker(
    config: MemoryRetrievalConfig,
    *,
    provider: CompletionProvider,
    cost_tracker: CostTrackerProtocol | None,
) -> QuerySpecificReranker | None:
    """Build the query-specific reranker when the config asks for it.

    Returns:
        The reranker, or ``None`` when reranking is disabled.
    """
    if not config.query_specific_rerank_enabled:
        return None
    return LLMQuerySpecificReranker(
        provider=provider,
        model=pin_for(PromptPurposeId.MEMORY_RERANK).model,
        cost_tracker=cost_tracker,
    )


def _build_hierarchical_retriever(
    config: MemoryRetrievalConfig,
    *,
    backend: MemoryBackend,
    provider: CompletionProvider,
    shared_store: SharedKnowledgeStore | None,
) -> HierarchicalRetriever | None:
    """Build the hierarchical retriever when the config selects it.

    Returns:
        The retriever, or ``None`` when the flat retriever is in use.
    """
    if config.retriever != "hierarchical":
        return None
    return create_hierarchical_retriever(
        config=config,
        backend=backend,
        provider=provider,
        model=pin_for(PromptPurposeId.MEMORY_RETRIEVAL_ROUTE).model,
        shared_store=shared_store,
    )


def _build_reformulation(
    config: MemoryRetrievalConfig,
    *,
    provider: CompletionProvider,
    cost_tracker: CostTrackerProtocol | None,
) -> tuple[QueryReformulator | None, SufficiencyChecker | None]:
    """Build the reformulator and sufficiency checker as a wired pair.

    Both or neither: the tool-based strategy runs its Search-and-Ask loop
    only when both are present, so pairing them here keeps that contract
    from depending on partial wiring.

    Returns:
        The ``(reformulator, sufficiency_checker)`` pair, both ``None``
        when reformulation is disabled.
    """
    if not config.query_reformulation_enabled:
        return None, None
    completion_fn = _retrieval_completion_fn(
        provider=provider,
        model=pin_for(PromptPurposeId.MEMORY_RETRIEVAL_RETRY).model,
        cost_tracker=cost_tracker,
    )
    return (
        LLMQueryReformulator(completion_fn=completion_fn),
        LLMSufficiencyChecker(completion_fn=completion_fn),
    )


def build_memory_injection_strategy_or_none(
    app_state: AppState,
    *,
    provider: CompletionProvider,
    cost_tracker: CostTrackerProtocol | None,
) -> MemoryInjectionStrategy | None:
    """Build the strategy that seeds memory into an agent's context.

    This is the seam the whole feature turns on: it is the one memory
    argument the engine was never constructed with, so
    ``_retrieve_injected_memory_messages`` short-circuited on every task
    and no agent ever saw a memory it had not explicitly asked for.

    The org layer arrives through ``shared_store``: without it the
    retrieval config's ``include_shared`` has nothing to include, so
    company-wide knowledge reaches an agent only if it thinks to call a
    Knowledge-Architect tool.

    The three LLM collaborators (reranker, hierarchical retriever,
    reformulation pair) are constructed here from the same explicit
    provider the engine dispatches on, each on its pinned model, whenever
    the retrieval config turns its stage on. Without this the strategy
    constructor raises the moment an operator enables ``rerank`` or
    ``hierarchical`` retrieval, taking down boot; the flags default off,
    so the crash was a latent one.

    Returns:
        The strategy, or ``None`` when no memory backend is wired, in
        which case the engine keeps its no-injection behaviour.
    """
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        return None
    config = app_state.config.memory.retrieval
    org_backend = org_memory_backend_of(app_state)
    shared_store: SharedKnowledgeStore | None = (
        OrgSharedKnowledgeStore(org_backend) if org_backend is not None else None
    )
    reformulator, sufficiency_checker = _build_reformulation(
        config, provider=provider, cost_tracker=cost_tracker
    )
    return build_memory_injection_strategy(
        config,
        backend=backend,
        token_estimator=DefaultTokenEstimator(),
        shared_store=shared_store,
        hierarchical_retriever=_build_hierarchical_retriever(
            config, backend=backend, provider=provider, shared_store=shared_store
        ),
        reranker=_build_reranker(config, provider=provider, cost_tracker=cost_tracker),
        reformulator=reformulator,
        sufficiency_checker=sufficiency_checker,
        self_editing_config=app_state.config.memory.self_editing,
    )


async def resolved_procedural_config(app_state: AppState) -> ProceduralMemoryConfig:
    """Return the procedural-memory config with the operator's current values.

    The proposer bakes the sampling parameters and the skill-file directory in
    at construction, and the boot config mirrors the directory from the
    environment only, so reading it alone would ignore a dashboard edit.

    Args:
        app_state: Application state carrying the boot config + resolver.

    Returns:
        The boot config with the resolved values applied.
    """
    namespace = SettingNamespace.MEMORY.value
    resolver = config_resolver_of(app_state)
    booted = app_state.config.memory.procedural
    directory = await resolver.get_str(namespace, "procedural_skill_md_directory")
    resolved = booted.model_dump() | {
        "temperature": await resolver.get_float(namespace, "procedural_temperature"),
        "max_tokens": await resolver.get_int(namespace, "procedural_max_tokens"),
        # An empty read is the documented "keep skills in the backend only",
        # and the field is NotBlankStr, so the sentinel maps to unset.
        "skill_md_directory": directory or None,
    }
    try:
        # Revalidated rather than copied in: model_copy skips validation
        # entirely, so a value outside the field's bounds (an env override is
        # never checked at write time) would reach the proposer as config the
        # model itself declares impossible.
        return ProceduralMemoryConfig.model_validate(resolved)
    except ValidationError as exc:
        logger.warning(
            PROCEDURAL_MEMORY_CONFIG_RESOLVE_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="resolved procedural settings are out of bounds; keeping boot config",
        )
        return booted


__all__ = [
    "build_memory_injection_strategy_or_none",
    "resolved_procedural_config",
    "wiki_exporter_or_none",
]

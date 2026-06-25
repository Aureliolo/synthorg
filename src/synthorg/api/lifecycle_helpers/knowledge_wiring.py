# module-kind: orchestrator
"""On-startup wiring for the knowledge + provenance substrate.

Retrieval always wires (it needs only persistence + a memory backend). The
generative-RAG synthesis arm is best-effort: it builds only when synthesis is
enabled (the default) AND a provider plus a non-blank model are configured;
otherwise the substrate is retrieval-only and the ``ask`` surface 503s with a
configure-a-model message rather than poisoning startup. Lives in its own
module so the feature-wiring orchestrator stays within its size budget.
"""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.knowledge.synthesis.protocol import Synthesizer

logger = get_logger(__name__)


async def wire_knowledge_engine(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
) -> None:
    """Wire the knowledge + provenance substrate once persistence + memory exist."""
    from synthorg.knowledge.state import KnowledgeStateSlice  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        MemoryStateSlice,
        memory_backend_of,
    )
    from synthorg.persistence.state import (  # noqa: PLC0415
        PersistenceStateSlice,
        persistence_of,
    )

    if app_state.slice(PersistenceStateSlice).backend is None:
        return
    if app_state.slice(KnowledgeStateSlice).service is not None:
        return
    config = app_state.config.knowledge
    if not config.enabled:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="knowledge substrate disabled (knowledge.enabled=false); skipped",
        )
        return
    if app_state.slice(MemoryStateSlice).backend is None:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="memory backend not wired; knowledge engine wiring skipped",
        )
        return
    from synthorg.knowledge.factory import build_knowledge_service  # noqa: PLC0415
    from synthorg.knowledge.tool_factory import (  # noqa: PLC0415
        build_knowledge_tool_factory,
    )

    synthesizer = await _maybe_build_knowledge_synthesizer(
        app_state, provider_registry=provider_registry
    )
    service = build_knowledge_service(
        memory_backend=memory_backend_of(app_state),
        persistence=persistence_of(app_state),
        config=config,
        synthesizer=synthesizer,
        clock=app_state.clock,
    )
    tool_factory = build_knowledge_tool_factory(service=service)
    app_state.swap_slice(
        KnowledgeStateSlice(service=service, tool_factory=tool_factory)
    )
    logger.info(
        API_APP_STARTUP,
        service="knowledge_engine",
        note="wired",
        synthesis="on" if synthesizer is not None else "off",
    )


async def _maybe_build_knowledge_synthesizer(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
) -> Synthesizer | None:
    """Build the knowledge synthesiser when enabled + a model is configured.

    Returns ``None`` (logged) when settings are unavailable, synthesis is
    disabled, no provider is registered, or no model is set, so the substrate
    degrades to retrieval-only rather than failing startup.

    Returns:
        A wired synthesiser, or ``None`` when synthesis is not configured.
    """
    from synthorg.budget.state import cost_tracker_of  # noqa: PLC0415
    from synthorg.knowledge.synthesis.factory import (  # noqa: PLC0415
        build_knowledge_synthesizer,
    )
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    runtime_settings = app_state.slice(SettingsStateSlice).settings_service
    if runtime_settings is None or provider_registry is None:
        return None
    enabled = (
        await runtime_settings.get("knowledge", "synthesis_enabled")
    ).value.strip().lower() == "true"
    model = (await runtime_settings.get("knowledge", "synthesis_model")).value.strip()
    if not enabled or not model:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="synthesis disabled or model unset; retrieval-only",
        )
        return None
    provider_names = provider_registry.list_providers()
    if not provider_names:
        return None
    provider_name = (
        await runtime_settings.get("knowledge", "synthesis_provider")
    ).value.strip()
    provider = (
        provider_registry.get(provider_name)
        if provider_name and provider_name in provider_registry
        else provider_registry.get(provider_names[0])
    )
    kind = (
        await runtime_settings.get("knowledge", "synthesis_synthesizer")
    ).value.strip()
    max_chunks = int(
        (await runtime_settings.get("knowledge", "synthesis_max_chunks")).value
    )
    return build_knowledge_synthesizer(
        kind=kind,
        provider=provider,
        model=model,
        max_chunks=max_chunks,
        clock=app_state.clock,
        cost_tracker=cost_tracker_of(app_state),
    )

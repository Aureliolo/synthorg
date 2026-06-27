# module-kind: orchestrator
"""On-startup wiring for the knowledge + provenance substrate.

Retrieval always wires (it needs only persistence + a memory backend). The
generative-RAG synthesis arm is best-effort: it builds only when synthesis is
enabled (the default) AND a provider plus a non-blank model are configured;
any failure to build it (missing dependency, bad setting value, unknown
strategy) degrades the substrate to retrieval-only and logs, rather than
poisoning the whole feature-wiring pass. Lives in its own module so the
feature-wiring orchestrator stays within its size budget.
"""

from typing import TYPE_CHECKING, Final

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from synthorg.knowledge.synthesis.protocol import Synthesizer

logger = get_logger(__name__)

# Governed ticket-fetch bounds. Sourced as module constants (not settings)
# because the fetcher is a fixed governed transport, not an operator knob;
# the SSRF policy below already fail-closes private hosts.
_TICKET_FETCH_TIMEOUT_SECONDS: Final[float] = 30.0
_TICKET_FETCH_MAX_BYTES: Final[int] = 5_242_880


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

    # Best-effort: a synthesis build failure leaves the substrate retrieval-only
    # rather than aborting the feature-wiring pass.
    try:
        synthesizer = await _maybe_build_knowledge_synthesizer(
            app_state, provider_registry=provider_registry
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised; synthesis is optional
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="synthesis build failed; retrieval-only",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        synthesizer = None
    # Governed ticket fetcher: routes ticket ingestion through the same
    # SSRF-validated, DNS-pinned egress the external-API tool uses, so a
    # malicious ticket URI cannot reach the host's internal network.
    from synthorg.knowledge.loaders.governed_ticket_fetcher import (  # noqa: PLC0415
        GovernedTicketFetcher,
    )
    from synthorg.tools.external_api.httpx_provider import (  # noqa: PLC0415
        HttpxExternalAccessProvider,
    )
    from synthorg.tools.network_validator import NetworkPolicy  # noqa: PLC0415

    ticket_fetcher = GovernedTicketFetcher(
        provider=HttpxExternalAccessProvider(),
        policy=NetworkPolicy(),
        timeout_seconds=_TICKET_FETCH_TIMEOUT_SECONDS,
        max_response_bytes=_TICKET_FETCH_MAX_BYTES,
    )
    service = build_knowledge_service(
        memory_backend=memory_backend_of(app_state),
        persistence=persistence_of(app_state),
        config=config,
        synthesizer=synthesizer,
        ticket_fetcher=ticket_fetcher,
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
    disabled, no model is set, or no usable provider is registered, so the
    substrate degrades to retrieval-only.

    Returns:
        A wired synthesiser, or ``None`` when synthesis is not configured.
    """
    from synthorg.budget.state import BudgetStateSlice  # noqa: PLC0415
    from synthorg.knowledge.synthesis.factory import (  # noqa: PLC0415
        build_knowledge_synthesizer,
    )
    from synthorg.settings.mirrors import parse_bool  # noqa: PLC0415
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    runtime_settings = app_state.slice(SettingsStateSlice).settings_service
    if runtime_settings is None or provider_registry is None:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="settings service or provider registry unavailable; retrieval-only",
        )
        return None
    enabled = parse_bool(
        (await runtime_settings.get("knowledge", "synthesis_enabled")).value
    )
    model = (await runtime_settings.get("knowledge", "synthesis_model")).value.strip()
    if not enabled or not model:
        logger.info(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="synthesis disabled or model unset; retrieval-only",
        )
        return None
    provider_name = (
        await runtime_settings.get("knowledge", "synthesis_provider")
    ).value.strip()
    provider = _resolve_synthesis_provider(provider_registry, provider_name)
    if provider is None:
        return None
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
        cost_tracker=app_state.slice(BudgetStateSlice).cost_tracker,
    )


def _resolve_synthesis_provider(
    provider_registry: ProviderRegistry,
    provider_name: str,
) -> CompletionProvider | None:
    """Resolve the synthesis provider, or ``None`` (logged) when unusable.

    An explicitly-configured provider name that is not registered is a
    misconfiguration: rather than silently substituting an arbitrary provider,
    the synthesis arm degrades to retrieval-only so the operator can correct it.

    Returns:
        The selected provider, or ``None`` when none is usable.
    """
    provider_names = provider_registry.list_providers()
    if not provider_names:
        logger.warning(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="synthesis enabled but no providers registered; retrieval-only",
        )
        return None
    if provider_name and provider_name not in provider_registry:
        logger.warning(
            API_APP_STARTUP,
            service="knowledge_engine",
            note="configured synthesis_provider not registered; retrieval-only",
            synthesis_provider=provider_name,
        )
        return None
    return provider_registry.get(provider_name or provider_names[0])

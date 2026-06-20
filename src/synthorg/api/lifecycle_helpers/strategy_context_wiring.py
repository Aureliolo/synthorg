# module-kind: orchestrator
"""On-startup wiring for the ambient strategic-context provider.

Resolves a :class:`StrategicContext` snapshot from the configured
``ContextSource`` (config / memory / meeting / composite) via
:func:`build_context`, binding the live memory backend and meeting
orchestrator, and publishes it on the process-global ambient holder the
synchronous prompt path reads. Refreshed once at boot; the snapshot is
slow-changing organisation-wide state.

Best-effort: a resolver failure leaves the ambient provider unbound so
the prompt path falls back to the static config context rather than
poisoning startup.
"""

from synthorg.api.state import AppState
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


async def wire_strategy_context(app_state: AppState) -> None:
    """Build, refresh, and bind the ambient strategic-context provider.

    Args:
        app_state: The application state holding config + collaborators.
    """
    from synthorg.communication.state import CommunicationStateSlice  # noqa: PLC0415
    from synthorg.engine.strategy.context import build_context  # noqa: PLC0415
    from synthorg.engine.strategy.models import StrategicContext  # noqa: PLC0415
    from synthorg.engine.strategy.strategic_context_provider import (  # noqa: PLC0415
        CachedStrategicContextProvider,
        set_strategic_context_provider,
    )
    from synthorg.memory.state import MemoryStateSlice  # noqa: PLC0415

    async def _resolve() -> StrategicContext:
        return await build_context(
            app_state.config.strategy,
            memory_backend=app_state.slice(MemoryStateSlice).backend,
            meeting_records=app_state.slice(
                CommunicationStateSlice
            ).meeting_orchestrator,
        )

    provider = CachedStrategicContextProvider(resolver=_resolve)
    try:
        await provider.refresh()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="strategy_context",
            note="strategic-context resolve failed; prompt path uses config",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    set_strategic_context_provider(provider)
    logger.info(API_APP_STARTUP, service="strategy_context", note="wired")


__all__ = ["wire_strategy_context"]

# module-kind: code
"""On-startup wiring for the operator console write-path service.

Wires the operator console behind its default-off toggle + fail-closed gate.
Lifted out of ``conversational_wiring`` so both stay within their size tier;
the combined :func:`wire_conversational_write_path` that brings this up
alongside the direct-MCP actor lives in ``conversational_wiring`` (which owns
the actor) to keep the module dependency acyclic.
"""

from synthorg.api.conversational_builders import build_operator_console
from synthorg.api.state import AppState
from synthorg.meta.chief_of_staff.console_conversation_store import (
    ConsoleConversationStore,
)
from synthorg.meta.config import SelfImprovementConfig
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)


def _console_conversation_store(app_state: AppState) -> ConsoleConversationStore:
    """Return the process-local console conversation store, creating it once.

    Held on ``MetaStateSlice`` so it is a stable singleton: a live console
    rebuild (toggle flip) reuses the same store, keeping in-flight conversation
    memory rather than dropping it on every reconfigure.

    Returns:
        The shared :class:`ConsoleConversationStore`.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    existing = app_state.slice(MetaStateSlice).console_conversation_store
    if existing is not None:
        return existing
    store = ConsoleConversationStore(clock=app_state.clock)
    app_state.wire_if_field_absent(MetaStateSlice, "console_conversation_store", store)
    wired = app_state.slice(MetaStateSlice).console_conversation_store
    return wired if wired is not None else store


async def wire_operator_console(
    app_state: AppState,
    *,
    si_config: SelfImprovementConfig,
) -> None:
    """Wire the operator console behind operator_console_enabled.

    Reuses the SHARED boot ``AgentEngine`` (held by the
    ``AgentEngineExecutionService``) so a sensitive configure step parks on
    the same ``ApprovalGate`` the ``/approvals`` controller resumes. Returns
    ``None`` -- leaving a CONFIGURE turn at 503 -- when the flag is off, no
    console model is bound, or no provider-backed boot engine was installed
    (empty company). Idempotent: a second boot pass skips when already wired.
    """
    from synthorg.integrations.state import IntegrationsStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.workers.execution_service import (  # noqa: PLC0415
        AgentEngineExecutionService,
    )
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    if app_state.slice(MetaStateSlice).operator_console is not None:
        return
    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    if not isinstance(service, AgentEngineExecutionService):
        return
    console = build_operator_console(
        si_config.chief_of_staff,
        engine=service.engine,
        autonomy_resolver=service.autonomy_resolver,
        clock=app_state.clock,
        secret_capture=app_state.slice(IntegrationsStateSlice).secret_capture_service,
        conversations=_console_conversation_store(app_state),
    )
    if console is not None:
        app_state.wire(MetaStateSlice, operator_console=console)
        logger.info(
            API_APP_STARTUP,
            service="operator_console",
            note="operator console wired",
        )


async def rebuild_operator_console(
    app_state: AppState,
    *,
    si_config: SelfImprovementConfig,
) -> None:
    """Unconditionally rebuild + swap the operator console from current config.

    Unlike the idempotent boot wirer, this always recomputes the console and
    swaps the slice to the fresh value, tearing it DOWN (to ``None``) when
    ``operator_console_enabled`` flips off or the console model is cleared. The
    rebuild re-runs the same fail-closed :func:`build_operator_console` gate
    (governance + MCP self-consumer must be wired, a model must be bound), so a
    live enable stays fail-closed. This is what lets the console toggle be
    hot-reloadable without weakening the startup security invariant.
    """
    from synthorg.integrations.state import IntegrationsStateSlice  # noqa: PLC0415
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415
    from synthorg.workers.execution_service import (  # noqa: PLC0415
        AgentEngineExecutionService,
    )
    from synthorg.workers.state import RuntimeStateSlice  # noqa: PLC0415

    service = app_state.slice(RuntimeStateSlice).worker_execution_service
    console = None
    if isinstance(service, AgentEngineExecutionService):
        console = build_operator_console(
            si_config.chief_of_staff,
            engine=service.engine,
            autonomy_resolver=service.autonomy_resolver,
            clock=app_state.clock,
            secret_capture=app_state.slice(
                IntegrationsStateSlice
            ).secret_capture_service,
            conversations=_console_conversation_store(app_state),
        )
    app_state.wire(MetaStateSlice, operator_console=console)
    logger.info(
        API_APP_STARTUP,
        service="operator_console",
        note="operator console rebuilt (live toggle)",
        wired=console is not None,
        enabled=si_config.chief_of_staff.operator_console_enabled,
    )


__all__ = [
    "rebuild_operator_console",
    "wire_operator_console",
]

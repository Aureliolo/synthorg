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
from synthorg.api.subsystems.errors import SubsystemDeclinedError
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
    the same ``ApprovalGate`` the ``/approvals`` controller resumes. A
    CONFIGURE turn stays at 503 while the console is not up, and the reason
    reaches ``GET /subsystems`` rather than only the boot log. Idempotent: a
    second boot pass skips when already wired.

    Raises:
        SubsystemDeclinedError: No provider-backed boot engine was installed
            (an empty company reaches this), or the builder's fail-closed
            gate refused for a condition it names.
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
        msg = (
            "no provider-backed boot engine is installed, so the console has "
            "nothing to dispatch through (an empty company reaches this)"
        )
        raise SubsystemDeclinedError(msg)
    console = build_operator_console(
        si_config.chief_of_staff,
        engine=service.engine,
        autonomy_resolver=service.autonomy_resolver,
        clock=app_state.clock,
        secret_capture=app_state.slice(IntegrationsStateSlice).secret_capture_service,
        conversations=_console_conversation_store(app_state),
    )
    app_state.wire(MetaStateSlice, operator_console=console)
    logger.info(
        API_APP_STARTUP,
        service="operator_console",
        note="operator console wired",
    )


async def unwire_operator_console(app_state: AppState) -> None:
    """Take the operator console down so the next pass rebuilds it.

    The reconciler pairs this with the wirer above on any change to the
    console toggle or model, which is what makes both live: teardown, then a
    rebuild that re-runs the same fail-closed
    :func:`build_operator_console` gate (governance and the MCP self-consumer
    must be wired, a model must be bound). A live enable therefore stays
    fail-closed, and a live disable genuinely removes the console instead of
    leaving the previous instance answering.
    """
    from synthorg.meta.state import MetaStateSlice  # noqa: PLC0415

    app_state.wire(MetaStateSlice, operator_console=None)
    logger.info(
        API_APP_STARTUP,
        service="operator_console",
        note="operator console unwired",
    )


__all__ = [
    "unwire_operator_console",
    "wire_operator_console",
]

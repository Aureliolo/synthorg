# module-kind: code
"""Startup wiring for meeting agent dispatch.

The meeting orchestrator is built during construction, because every other
meeting surface binds a reference to it there. Its agent caller cannot be:
real dispatch is composed from the provider registry, which does not exist
until persistence is up. So the orchestrator ships with a caller that
refuses every turn, and this hook installs the real one on the reconcile
pass where both registries are present.

Without it the refusal is permanent. Nothing else rebuilds the caller, so
every meeting the organisation runs -- ceremonies, retros, the conflict
panel -- raises :class:`MeetingAgentCallerNotConfiguredError` at the first
turn on a deployment that is otherwise fully configured.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.communication.meeting.agent_caller import build_meeting_agent_caller
from synthorg.communication.state import CommunicationStateSlice
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.providers.state import ProvidersStateSlice

logger = get_logger(__name__)


async def wire_meeting_agent_dispatch(app_state: AppState) -> None:
    """Install real LLM dispatch on the live meeting orchestrator.

    Idempotent: an orchestrator already dispatching is left alone, so a
    later pass cannot swap the caller under a meeting in flight.

    Args:
        app_state: The application state holding the collaborator slices.

    Raises:
        SubsystemDeclinedError: The orchestrator or a registry the caller
            is composed from is not wired yet, naming which.
    """
    orchestrator = app_state.slice(CommunicationStateSlice).meeting_orchestrator
    if orchestrator is None:
        msg = "no meeting orchestrator; there is nothing to install dispatch on"
        raise SubsystemDeclinedError(msg)
    if orchestrator.has_agent_dispatch:
        return

    agent_registry = app_state.slice(HrStateSlice).agent_registry
    provider_registry = app_state.slice(ProvidersStateSlice).registry
    missing = [
        name
        for name, present in (
            ("agent registry", agent_registry is not None),
            ("provider registry", provider_registry is not None),
        )
        if not present
    ]
    if agent_registry is None or provider_registry is None:
        msg = f"waiting on: {', '.join(missing)}"
        raise SubsystemDeclinedError(msg)

    orchestrator.set_agent_caller(
        build_meeting_agent_caller(
            agent_registry=agent_registry,
            provider_registry=provider_registry,
        )
    )
    logger.info(API_APP_STARTUP, service="meeting_agent_dispatch", note="wired")


__all__ = ["wire_meeting_agent_dispatch"]

# module-kind: orchestrator
"""Startup wiring for the meeting scheduler and the ceremony scheduler.

The two are one subsystem because the ceremony scheduler is a thin
coordinator over the meeting scheduler: it holds the instance, so a
ceremony scheduler built around a scheduler somebody else owns would be a
second reference to a service with two lifecycles.

Both used to be built at construction, guarded on a provider registry that
is always absent there, and nothing re-ran the guard. So a configured
deployment ran no scheduled meetings at all: no standups, no retros, no
ceremonies, and ``sprint_service`` declined for the whole process with
"waiting on: ceremony scheduler" against a dependency no pass could supply.

Building it here makes that wait a real unmet capability the reconciler
reports and then satisfies.
"""

from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.participant import ParticipantResolver
from synthorg.communication.meeting.scheduler import (
    MeetingEventPublisher,
    MeetingScheduler,
)
from synthorg.communication.state import CommunicationStateSlice
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.state import HrStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.state import PersistenceStateSlice

logger = get_logger(__name__)


async def wire_ceremony_scheduler(app_state: AppState) -> None:
    """Build, start and wire the meeting + ceremony schedulers.

    Idempotent in both halves: an already-wired scheduler is reused rather
    than replaced (every ceremony holds the instance), and starting is
    guarded on the scheduler's own ``running`` flag.

    Args:
        app_state: The application state holding the collaborator slices.

    Raises:
        SubsystemDeclinedError: A collaborator the schedulers are built
            from is not wired yet, naming which.
    """
    communication = app_state.slice(CommunicationStateSlice)
    orchestrator = communication.meeting_orchestrator
    persistence = app_state.slice(PersistenceStateSlice).backend
    agent_registry = app_state.slice(HrStateSlice).agent_registry
    missing = [
        name
        for name, present in (
            ("meeting orchestrator", orchestrator is not None),
            ("persistence backend", persistence is not None),
            ("agent registry", agent_registry is not None),
        )
        if not present
    ]
    if orchestrator is None or persistence is None or agent_registry is None:
        msg = f"waiting on: {', '.join(missing)}"
        raise SubsystemDeclinedError(msg)

    scheduler = communication.meeting_scheduler
    if scheduler is None:
        scheduler = _build_meeting_scheduler(
            app_state,
            orchestrator=orchestrator,
            agent_registry=agent_registry,
            persistence=persistence,
            event_publisher=communication.meeting_event_publisher,
        )
        app_state.wire(CommunicationStateSlice, meeting_scheduler=scheduler)
        logger.info(API_APP_STARTUP, service="meeting_scheduler", note="wired")

    from synthorg.api.lifecycle_startup import _reset_if_tasks_dead  # noqa: PLC0415

    # A scheduler whose loop was cancelled with a closed event loop still
    # reads running, so an app re-entering its lifespan would find one wired
    # and never restart it.
    _reset_if_tasks_dead(scheduler, "_running", "_tasks")
    if not scheduler.running:
        await scheduler.start()
        logger.info(API_APP_STARTUP, service="meeting_scheduler", note="started")

    if app_state.slice(EngineStateSlice).ceremony_scheduler is None:
        app_state.wire(
            EngineStateSlice,
            ceremony_scheduler=CeremonyScheduler(
                meeting_scheduler=scheduler,
                state_repo=(
                    persistence.ceremony_scheduler_state
                    if persistence.is_connected
                    else None
                ),
            ),
        )
        logger.info(API_APP_STARTUP, service="ceremony_scheduler", note="wired")


def _build_meeting_scheduler(
    app_state: AppState,
    *,
    orchestrator: MeetingOrchestrator,
    agent_registry: AgentRegistryService,
    persistence: PersistenceBackend,
    event_publisher: MeetingEventPublisher | None,
) -> MeetingScheduler:
    """Compose the meeting scheduler from live collaborators.

    Args:
        app_state: The application state, read for the meetings + strategy
            configuration.
        orchestrator: The orchestrator meetings run through.
        agent_registry: Resolves participant roles to agent identities.
        persistence: Supplies the cooldown repository, so per-meeting-type
            cooldowns survive a restart.
        event_publisher: Delivers meeting events to the WebSocket channel,
            or ``None`` when the app was built without one.

    Returns:
        A configured ``MeetingScheduler``.
    """
    from synthorg.api._meeting_strategy_dispatch import (  # noqa: PLC0415
        build_budget_scaler,
    )
    from synthorg.communication.meeting.participant import (  # noqa: PLC0415
        RegistryParticipantResolver,
    )

    resolver: ParticipantResolver = RegistryParticipantResolver(agent_registry)
    config = app_state.config
    return MeetingScheduler(
        config=config.communication.meetings,
        orchestrator=orchestrator,
        participant_resolver=resolver,
        cooldown_repo=(
            persistence.meeting_cooldown if persistence.is_connected else None
        ),
        budget_scaler=build_budget_scaler(config.strategy),
        event_publisher=event_publisher,
    )


__all__ = ["wire_ceremony_scheduler"]

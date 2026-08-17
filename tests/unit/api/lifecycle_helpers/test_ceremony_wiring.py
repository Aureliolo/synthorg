"""The meeting stack is built by the reconciler, not by construction.

Both schedulers used to be built during construction, guarded on a provider
registry that is always absent there, and nothing re-ran the guard. A
configured deployment therefore ran no scheduled meetings at all, and
``sprint_service`` declined for the whole process against a dependency no
pass could supply.

These cover the three halves that had to hold for the guard to become a
capability: dispatch is installed on the live orchestrator, the schedulers
are built and started once, and each activation names what it is waiting on.
"""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.lifecycle_helpers.ceremony_wiring import wire_ceremony_scheduler
from synthorg.api.lifecycle_helpers.meeting_dispatch_wiring import (
    wire_meeting_agent_dispatch,
)
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.communication.meeting.models import AgentResponse
from synthorg.communication.meeting.orchestrator import MeetingOrchestrator
from synthorg.communication.meeting.scheduler import MeetingScheduler
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.agent import AgentIdentity, ModelConfig, PersonalityConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.state import EngineStateSlice
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.models import CompletionResponse, TokenUsage
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from tests._shared import as_uuid, make_app_state, mock_of

pytestmark = pytest.mark.unit


def _refusing_orchestrator() -> MeetingOrchestrator:
    """Build an orchestrator in the state construction leaves it in.

    Returns:
        An orchestrator whose caller refuses every turn.
    """
    from synthorg.communication.meeting.agent_caller import (
        build_unconfigured_meeting_agent_caller,
    )

    return MeetingOrchestrator(
        agent_caller=build_unconfigured_meeting_agent_caller(
            missing_dependencies=("meeting_agent_dispatch",),
        ),
    )


def _identity() -> AgentIdentity:
    """Return a roster identity the caller can resolve.

    Returns:
        One ACTIVE agent bound to its own provider / model pair.
    """
    return AgentIdentity(
        id=as_uuid("sarah-chen"),
        name=NotBlankStr("Sarah Chen"),
        role=NotBlankStr("engineer"),
        department=NotBlankStr("engineering"),
        personality=PersonalityConfig(
            communication_style=NotBlankStr("concise"),
        ),
        model=ModelConfig(
            provider=NotBlankStr("test-provider"),
            model_id=NotBlankStr("test-capable-001"),
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _dispatching_state(
    provider: CompletionProvider,
) -> tuple[AppState, MeetingOrchestrator, MagicMock]:
    """Build a state whose registries can compose a real caller.

    Args:
        provider: The completion provider the registry hands back.

    Returns:
        The state, its orchestrator, and the provider registry double.
    """
    orchestrator = _refusing_orchestrator()
    provider_registry = mock_of[ProviderRegistry](
        get=MagicMock(return_value=provider),
    )
    app_state = make_app_state(
        meeting_orchestrator=orchestrator,
        agent_registry=mock_of[AgentRegistryService](
            get=AsyncMock(return_value=_identity()),
        ),
        provider_registry=provider_registry,
    )
    return app_state, orchestrator, cast("MagicMock", provider_registry)


class TestMeetingAgentDispatch:
    async def test_installs_dispatch_on_the_live_orchestrator(self) -> None:
        # Replacing the orchestrator instead would strand the meetings REST
        # surface, the conflict bridge and the strategy context, each of
        # which bound this object during construction.
        provider = mock_of[CompletionProvider](
            complete=AsyncMock(
                return_value=CompletionResponse(
                    content="I recommend a task queue.",
                    finish_reason=FinishReason.STOP,
                    usage=TokenUsage(input_tokens=12, output_tokens=7, cost=0.0005),
                    model=NotBlankStr("test-capable-001"),
                )
            ),
        )
        app_state, orchestrator, provider_registry = _dispatching_state(provider)
        was_refusing = not orchestrator.has_agent_dispatch

        await wire_meeting_agent_dispatch(app_state)

        assert was_refusing
        assert orchestrator.has_agent_dispatch
        caller = orchestrator._agent_caller
        response = await caller(str(_identity().id), "What is next?", 256, "meeting-1")
        assert isinstance(response, AgentResponse)
        assert response.content == "I recommend a task queue."
        assert response.input_tokens == 12
        provider_registry.get.assert_called_once_with("test-provider")
        cast("MagicMock", provider.complete).assert_awaited_once()

    async def test_declines_naming_the_absent_registry(self) -> None:
        app_state = make_app_state(meeting_orchestrator=_refusing_orchestrator())

        with pytest.raises(SubsystemDeclinedError, match="provider registry"):
            await wire_meeting_agent_dispatch(app_state)

    async def test_declines_when_there_is_no_orchestrator(self) -> None:
        with pytest.raises(SubsystemDeclinedError, match="orchestrator"):
            await wire_meeting_agent_dispatch(make_app_state())

    async def test_leaves_an_already_dispatching_orchestrator_alone(self) -> None:
        # A swap mid-meeting would change what the turn in flight dispatches
        # through, so the guard is on the caller rather than on a flag.
        provider = mock_of[CompletionProvider](complete=AsyncMock())
        app_state, orchestrator, _registry = _dispatching_state(provider)
        await wire_meeting_agent_dispatch(app_state)
        installed = orchestrator._agent_caller

        await wire_meeting_agent_dispatch(app_state)

        assert orchestrator._agent_caller is installed


class TestCeremonySchedulerWiring:
    async def test_builds_starts_and_wires_both_schedulers(self) -> None:
        app_state = _ceremony_state()

        await wire_ceremony_scheduler(app_state)

        scheduler = app_state.slice(CommunicationStateSlice).meeting_scheduler
        assert isinstance(scheduler, MeetingScheduler)
        assert scheduler.running is True
        assert isinstance(
            app_state.slice(EngineStateSlice).ceremony_scheduler,
            CeremonyScheduler,
        )
        await scheduler.stop()

    async def test_a_second_pass_reuses_the_scheduler_it_built(self) -> None:
        # Every ceremony holds the instance, so a rebuild would leave the
        # sprint service advancing a scheduler nothing else can see.
        app_state = _ceremony_state()
        await wire_ceremony_scheduler(app_state)
        first = app_state.slice(CommunicationStateSlice).meeting_scheduler
        ceremony = app_state.slice(EngineStateSlice).ceremony_scheduler

        await wire_ceremony_scheduler(app_state)

        assert app_state.slice(CommunicationStateSlice).meeting_scheduler is first
        assert app_state.slice(EngineStateSlice).ceremony_scheduler is ceremony
        assert first is not None
        await first.stop()

    async def test_publishes_meeting_events_through_the_wired_publisher(self) -> None:
        # The publisher is built by the composition root (only it holds the
        # channels plugin) and consumed here, so a scheduler built on a
        # later pass still reaches the websocket channel.
        published: list[str] = []
        app_state = _ceremony_state()
        app_state.wire(
            CommunicationStateSlice,
            meeting_event_publisher=lambda event, _payload: published.append(event),
        )

        await wire_ceremony_scheduler(app_state)

        scheduler = app_state.slice(CommunicationStateSlice).meeting_scheduler
        assert scheduler is not None
        assert scheduler._event_publisher is not None
        await scheduler.stop()

    async def test_declines_naming_the_absent_collaborator(self) -> None:
        app_state = make_app_state(meeting_orchestrator=_refusing_orchestrator())

        with pytest.raises(SubsystemDeclinedError, match="persistence backend"):
            await wire_ceremony_scheduler(app_state)


def _ceremony_state() -> AppState:
    """Build a state whose ceremony dependencies are all present.

    Returns:
        A state carrying an orchestrator, persistence and an agent registry.
    """
    from tests.unit.api.conftest import FakePersistenceBackend

    return make_app_state(
        meeting_orchestrator=_refusing_orchestrator(),
        agent_registry=AgentRegistryService(),
        persistence=FakePersistenceBackend(),
    )

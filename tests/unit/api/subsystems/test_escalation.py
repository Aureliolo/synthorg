"""A subsystem that cannot come up has to reach the operator, once.

``GET /subsystems`` already answers "why is this not up" for whoever asks.
Nothing asked: a memory backend blocked on an unreachable embedding model sat
that way through a working session while every agent ran with no recall, and
the only trace was a health field and a log line.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.api.subsystems.escalation import SubsystemEscalator
from synthorg.api.subsystems.report import SubsystemStatus
from synthorg.api.subsystems.spec import CapabilityId, SubsystemPhase
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import Notification, NotificationSeverity
from synthorg.notifications.state import NotificationsStateSlice
from tests._shared import make_app_state, mock_of


def _blocked(name: str = "memory_backend", detail: str = "unset: x") -> SubsystemStatus:
    """Build a BLOCKED status.

    Returns:
        The status, carrying *detail* as its decline reason.
    """
    return SubsystemStatus(name=name, phase=SubsystemPhase.BLOCKED, detail=detail)


def _dispatched(dispatcher: AsyncMock) -> list[Notification]:
    """Every notification handed to *dispatcher*.

    Returns:
        The notifications, in dispatch order.
    """
    return [call.args[0] for call in dispatcher.dispatch.await_args_list]


@pytest.fixture
def wired() -> tuple[AppState, AsyncMock]:
    """An app state with a dispatcher double wired in.

    Returns:
        The state and the dispatcher double it carries.
    """
    dispatcher = mock_of[NotificationDispatcher](dispatch=AsyncMock())
    app_state = make_app_state(
        slices={NotificationsStateSlice: {"dispatcher": dispatcher}}
    )
    return app_state, dispatcher


@pytest.mark.unit
class TestSubsystemEscalation:
    async def test_a_blocked_subsystem_notifies(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        app_state, dispatcher = wired
        await SubsystemEscalator().escalate(app_state, [_blocked()])

        sent = _dispatched(dispatcher)
        assert len(sent) == 1
        assert sent[0].severity is NotificationSeverity.WARNING
        # The reason travels with it: an alert naming only the subsystem
        # leaves the operator exactly where the log line did.
        assert "unset: x" in sent[0].body
        assert "memory_backend" in sent[0].title

    async def test_the_same_condition_notifies_once(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        """The reconciler runs a full pass on every settings write and sweep.

        Alerting per pass would turn one unreachable embedder into a
        notification every sweep until it was fixed, and an operator would
        filter the channel.
        """
        app_state, dispatcher = wired
        escalator = SubsystemEscalator()
        for _ in range(5):
            await escalator.escalate(app_state, [_blocked()])

        assert len(_dispatched(dispatcher)) == 1

    async def test_a_new_reason_notifies_again(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        app_state, dispatcher = wired
        escalator = SubsystemEscalator()
        await escalator.escalate(app_state, [_blocked(detail="unset: embedder_dims")])
        await escalator.escalate(app_state, [_blocked(detail="persistence gone")])

        assert len(_dispatched(dispatcher)) == 2

    async def test_a_fault_that_returns_after_recovery_notifies_again(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        """Remembering forever would silence the second outage."""
        app_state, dispatcher = wired
        escalator = SubsystemEscalator()
        await escalator.escalate(app_state, [_blocked()])
        await escalator.escalate(
            app_state,
            [SubsystemStatus(name="memory_backend", phase=SubsystemPhase.ACTIVE)],
        )
        await escalator.escalate(app_state, [_blocked()])

        assert len(_dispatched(dispatcher)) == 2

    async def test_a_failed_subsystem_notifies_at_error(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        app_state, dispatcher = wired
        await SubsystemEscalator().escalate(
            app_state,
            [
                SubsystemStatus(
                    name="pruning_service",
                    phase=SubsystemPhase.FAILED,
                    detail="wiring raised",
                )
            ],
        )

        assert _dispatched(dispatcher)[0].severity is NotificationSeverity.ERROR

    @pytest.mark.parametrize(
        "status",
        [
            SubsystemStatus(name="s", phase=SubsystemPhase.ACTIVE),
            SubsystemStatus(name="s", phase=SubsystemPhase.DISABLED),
            SubsystemStatus(
                name="s",
                phase=SubsystemPhase.WAITING,
                waiting_on=(CapabilityId.PERSISTENCE,),
            ),
        ],
    )
    async def test_resting_states_do_not_notify(
        self, wired: tuple[AppState, AsyncMock], status: SubsystemStatus
    ) -> None:
        """Waiting and disabled are how things are, not faults.

        Alerting on them would bury the two phases that mean a person has to
        do something.
        """
        app_state, dispatcher = wired
        await SubsystemEscalator().escalate(app_state, [status])

        assert not _dispatched(dispatcher)

    async def test_an_unwired_dispatcher_is_not_an_error(self) -> None:
        """This runs at the tail of a pass that already converged."""
        await SubsystemEscalator().escalate(make_app_state(), [_blocked()])

    async def test_a_failing_sink_does_not_propagate(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        app_state, dispatcher = wired
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("sink down"))

        await SubsystemEscalator().escalate(app_state, [_blocked()])

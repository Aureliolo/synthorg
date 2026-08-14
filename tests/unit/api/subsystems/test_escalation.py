"""A subsystem that cannot come up has to reach the operator, once.

``GET /subsystems`` already answers "why is this not up" for whoever asks, and
nothing in the system asks. A subsystem that declines is therefore visible only
to someone already reading a health payload or a log stream, so it can stay
down indefinitely while the org keeps executing around the hole.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.state import AppState
from synthorg.api.subsystems.escalation import SubsystemEscalator
from synthorg.api.subsystems.report import SubsystemStatus
from synthorg.api.subsystems.spec import CapabilityId, SubsystemPhase
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import Notification, NotificationSeverity
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.observability.events.api import API_SUBSYSTEM_ESCALATION_UNROUTED
from tests._shared import make_app_state, mock_of


def _blocked(name: str = "memory_backend", detail: str = "unset: x") -> SubsystemStatus:
    """Build a BLOCKED status.

    Returns:
        The status, carrying *detail* as its decline reason.
    """
    return SubsystemStatus(name=name, phase=SubsystemPhase.BLOCKED, detail=detail)


def _dispatched(dispatcher: AsyncMock) -> list[Notification]:
    """Every notification handed to *dispatcher*.

    Bound against the real signature rather than read off ``call.args``, so a
    caller that starts passing the notification by keyword keeps these tests
    honest instead of failing them on an index that no longer exists.

    Returns:
        The notifications, in dispatch order.
    """
    signature = inspect.signature(NotificationDispatcher.dispatch)
    return [
        signature.bind(None, *call.args, **call.kwargs).arguments["notification"]
        for call in dispatcher.dispatch.await_args_list
    ]


@pytest.fixture
def wired() -> tuple[AppState, AsyncMock]:
    """An app state with a dispatcher double wired in.

    Returns:
        The state and the dispatcher double it carries.
    """
    dispatcher = mock_of[NotificationDispatcher]()
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
            SubsystemStatus(
                name="s",
                phase=SubsystemPhase.UNREACHABLE,
                detail="owner disabled",
            ),
        ],
    )
    async def test_resting_states_do_not_notify(
        self, wired: tuple[AppState, AsyncMock], status: SubsystemStatus
    ) -> None:
        """Waiting and disabled are how things are, not faults.

        Alerting on them would bury the two phases that mean a person has to
        do something. UNREACHABLE reads like a third but is not: it is only
        produced for a subsystem whose dependency has a BLOCKED or DISABLED
        owner, so alerting would either repeat that owner's own alert or
        report an operator's own switch back to them.
        """
        app_state, dispatcher = wired
        await SubsystemEscalator().escalate(app_state, [status])

        assert not _dispatched(dispatcher)

    async def test_an_unwired_dispatcher_is_not_an_error(self) -> None:
        """This runs at the tail of a pass that already converged."""
        await SubsystemEscalator().escalate(make_app_state(), [_blocked()])

    async def test_an_unwired_dispatcher_says_so_once(self) -> None:
        """Otherwise it looks identical to healthy sinks with nothing to say."""
        escalator = SubsystemEscalator()
        app_state = make_app_state()
        with capture_logs() as logs:
            await escalator.escalate(app_state, [_blocked(name="a")])
            await escalator.escalate(app_state, [_blocked(name="b")])

        unrouted = [
            entry
            for entry in logs
            if entry["event"] == API_SUBSYSTEM_ESCALATION_UNROUTED
        ]
        assert len(unrouted) == 1
        assert unrouted[0]["log_level"] == "error"

    async def test_a_failing_sink_does_not_propagate(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        app_state, dispatcher = wired
        dispatcher.dispatch = AsyncMock(
            spec=NotificationDispatcher.dispatch,
            side_effect=RuntimeError("sink down"),
        )

        await SubsystemEscalator().escalate(app_state, [_blocked()])

    async def test_a_failing_sink_leaves_the_condition_unremembered(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        """Nothing in the dispatch chain retries, so the escalator must.

        Remembering a condition the sink never accepted would let one
        transient outage suppress that exact condition permanently, however
        many passes the reconciler runs while the subsystem stays stuck.
        """
        app_state, dispatcher = wired
        dispatcher.dispatch = AsyncMock(
            spec=NotificationDispatcher.dispatch,
            side_effect=RuntimeError("sink down"),
        )
        escalator = SubsystemEscalator()
        await escalator.escalate(app_state, [_blocked()])

        dispatcher.dispatch = AsyncMock(spec=NotificationDispatcher.dispatch)
        await escalator.escalate(app_state, [_blocked()])

        assert len(_dispatched(dispatcher)) == 1

    async def test_many_stuck_subsystems_dispatch_concurrently(
        self, wired: tuple[AppState, AsyncMock]
    ) -> None:
        """The reconciler awaits this while holding its pass lock.

        Sequentially, a broad outage that blocks many subsystems at once would
        hold that lock for the sum of every sink's timeout.
        """
        app_state, dispatcher = wired
        in_flight = 0
        peak = 0

        async def _slow(notification: Notification) -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1

        dispatcher.dispatch = AsyncMock(
            spec=NotificationDispatcher.dispatch, side_effect=_slow
        )
        stuck = [_blocked(name=f"s{i}") for i in range(4)]
        await SubsystemEscalator().escalate(app_state, stuck)

        assert peak > 1

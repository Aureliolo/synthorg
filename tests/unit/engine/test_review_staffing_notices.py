"""The staffing alerts, and the one thing about how they are sent.

The copy itself is asserted where it is used. What is pinned here is the
late binding: the dispatcher is asked for per send rather than captured,
because a settings write that rewires notifications closes the instance
that was current. A held one is already shut by the time the first role
goes unstaffed, so the alert that matters most is the one that vanishes.
"""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.engine.review_staffing.notices import (
    ACTOR,
    DispatcherSource,
    notify_hire_waiting,
    notify_standing_gap,
)
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestLateBinding:
    async def test_the_source_is_asked_once_per_send(self) -> None:
        dispatch = AsyncMock()
        calls = 0

        def _source() -> NotificationDispatcher:
            nonlocal calls
            calls += 1
            fresh: NotificationDispatcher = mock_of[NotificationDispatcher](
                dispatch=dispatch
            )
            return fresh

        await notify_standing_gap(_source, COMPLETION_REVIEWER_ROLE_NAME)
        await notify_standing_gap(_source, COMPLETION_REVIEWER_ROLE_NAME)

        assert calls == 2

    async def test_a_rewired_dispatcher_is_the_one_used(self) -> None:
        """The whole point of not capturing: the second send goes elsewhere."""
        first = AsyncMock()
        second = AsyncMock()
        queue: list[NotificationDispatcher] = [
            mock_of[NotificationDispatcher](dispatch=first),
            mock_of[NotificationDispatcher](dispatch=second),
        ]

        def _source() -> NotificationDispatcher:
            return queue.pop(0)

        await notify_standing_gap(_source, COMPLETION_REVIEWER_ROLE_NAME)
        await notify_hire_waiting(_source, COMPLETION_REVIEWER_ROLE_NAME)

        first.assert_awaited_once()
        second.assert_awaited_once()

    async def test_an_unwired_source_sends_nothing(self) -> None:
        # Notifications are optional wiring, not a failure to report.
        await notify_standing_gap(None, COMPLETION_REVIEWER_ROLE_NAME)
        await notify_hire_waiting(None, COMPLETION_REVIEWER_ROLE_NAME)

    async def test_a_source_answering_none_sends_nothing(self) -> None:
        # The subsystem is declared but not up yet; the pass runs from boot.
        await notify_standing_gap(lambda: None, COMPLETION_REVIEWER_ROLE_NAME)
        await notify_hire_waiting(lambda: None, COMPLETION_REVIEWER_ROLE_NAME)


class TestTheTwoAlerts:
    """They answer different questions, so they are routed differently."""

    @staticmethod
    async def _sent(
        send: Callable[[DispatcherSource, str], Awaitable[None]], role: str
    ) -> Notification:
        """Send one alert and return what reached the dispatcher.

        Returns:
            The dispatched notification.
        """
        dispatch = AsyncMock()
        await send(lambda: mock_of[NotificationDispatcher](dispatch=dispatch), role)
        sent = dispatch.await_args_list[0].args[0]
        assert isinstance(sent, Notification)
        return sent

    async def test_the_standing_gap_is_a_system_warning(self) -> None:
        sent = await self._sent(notify_standing_gap, COMPLETION_REVIEWER_ROLE_NAME)

        assert sent.category is NotificationCategory.SYSTEM
        assert sent.severity is NotificationSeverity.WARNING
        assert str(sent.title) == f"No agent holds {COMPLETION_REVIEWER_ROLE_NAME}"
        assert str(sent.source) == ACTOR

    async def test_the_hire_alert_is_an_approval(self) -> None:
        # It says there is something to decide, so it belongs where the
        # operator looks for decisions rather than beside a status warning.
        sent = await self._sent(notify_hire_waiting, COMPLETION_REVIEWER_ROLE_NAME)

        assert sent.category is NotificationCategory.APPROVAL
        assert sent.severity is NotificationSeverity.WARNING
        assert str(sent.source) == ACTOR

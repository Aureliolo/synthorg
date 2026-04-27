"""Tests for the NotificationDispatcher."""

import pytest

from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)


def _make_notification(
    *,
    severity: NotificationSeverity = NotificationSeverity.WARNING,
    category: NotificationCategory = NotificationCategory.BUDGET,
) -> Notification:
    return Notification(
        category=category,
        severity=severity,
        title="Test notification",
        source="test",
    )


class _FakeSink:
    """Test sink that records calls."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[Notification] = []
        self._fail = fail

    @property
    def sink_name(self) -> str:
        return "fake"

    async def send(self, notification: Notification) -> None:
        if self._fail:
            msg = "sink failed"
            raise RuntimeError(msg)
        self.calls.append(notification)

    async def start(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""

    async def close(self) -> None:
        """No-op (stateless sink); satisfies the protocol."""


@pytest.mark.unit
class TestNotificationDispatcher:
    async def test_dispatches_to_all_sinks(self) -> None:
        s1 = _FakeSink()
        s2 = _FakeSink()
        dispatcher = NotificationDispatcher(sinks=(s1, s2))

        n = _make_notification()
        await dispatcher.dispatch(n)

        assert len(s1.calls) == 1
        assert len(s2.calls) == 1
        assert s1.calls[0].id == n.id

    async def test_individual_sink_failure_does_not_block_others(
        self,
    ) -> None:
        s_ok = _FakeSink()
        s_fail = _FakeSink(fail=True)
        dispatcher = NotificationDispatcher(sinks=(s_fail, s_ok))

        n = _make_notification()
        await dispatcher.dispatch(n)

        assert len(s_ok.calls) == 1
        assert len(s_fail.calls) == 0

    async def test_empty_sinks_is_noop(self) -> None:
        dispatcher = NotificationDispatcher(sinks=())
        await dispatcher.dispatch(_make_notification())

    async def test_register_adds_sink(self) -> None:
        dispatcher = NotificationDispatcher()
        s = _FakeSink()
        dispatcher.register(s)

        await dispatcher.dispatch(_make_notification())
        assert len(s.calls) == 1

    async def test_filters_below_min_severity(self) -> None:
        s = _FakeSink()
        dispatcher = NotificationDispatcher(
            sinks=(s,),
            min_severity=NotificationSeverity.WARNING,
        )

        await dispatcher.dispatch(
            _make_notification(severity=NotificationSeverity.INFO)
        )
        assert len(s.calls) == 0

    async def test_passes_at_min_severity(self) -> None:
        s = _FakeSink()
        dispatcher = NotificationDispatcher(
            sinks=(s,),
            min_severity=NotificationSeverity.WARNING,
        )

        await dispatcher.dispatch(
            _make_notification(severity=NotificationSeverity.WARNING)
        )
        assert len(s.calls) == 1

    async def test_passes_above_min_severity(self) -> None:
        s = _FakeSink()
        dispatcher = NotificationDispatcher(
            sinks=(s,),
            min_severity=NotificationSeverity.WARNING,
        )

        await dispatcher.dispatch(
            _make_notification(severity=NotificationSeverity.CRITICAL)
        )
        assert len(s.calls) == 1

    async def test_memory_error_propagates(self) -> None:
        class _MemSink:
            @property
            def sink_name(self) -> str:
                return "mem"

            async def send(self, notification: Notification) -> None:
                raise MemoryError

            async def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        dispatcher = NotificationDispatcher(sinks=(_MemSink(),))
        with pytest.raises(MemoryError):
            await dispatcher.dispatch(_make_notification())

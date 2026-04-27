"""Tests for the NotificationSink protocol."""

import pytest

from synthorg.notifications.models import (
    Notification,
)
from synthorg.notifications.protocol import NotificationSink


class _GoodSink:
    """Structural match for NotificationSink."""

    @property
    def sink_name(self) -> str:
        return "good"

    async def send(self, notification: Notification) -> None:
        pass

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


class _BadSink:
    """Missing sink_name property."""

    async def send(self, notification: Notification) -> None:
        pass

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.unit
class TestNotificationSinkProtocol:
    def test_isinstance_check_passes_for_structural_match(self) -> None:
        sink = _GoodSink()
        assert isinstance(sink, NotificationSink)

    def test_isinstance_check_fails_for_incomplete_impl(self) -> None:
        sink = _BadSink()
        assert not isinstance(sink, NotificationSink)

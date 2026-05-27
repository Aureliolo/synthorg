# mypy: disable-error-code="explicit-any"
"""Lifecycle contract tests for HTTP-bearing notification sinks.

Pins the resource-hygiene rule that ``SlackNotificationSink`` and
``NtfyNotificationSink``:

1. Do **not** create the ``httpx.AsyncClient`` in ``__init__`` (zero
   leak when a sink is constructed but never started).
2. Open the client lazily inside ``start()``.
3. Close the client deterministically inside ``close()`` and
   ``__aexit__``.
4. Refuse to send before ``start()`` (loud RuntimeError, not an
   ambiguous ``AttributeError`` from ``None.post``).
5. Tolerate idempotent / out-of-order calls (double start, double
   close, close-before-start, concurrent start).

The dispatcher tests below exercise the same lifecycle through the
fan-out + ``_safe_start`` / ``_safe_close`` helpers.
"""

import asyncio
from typing import TYPE_CHECKING, override
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response

from synthorg.notifications.adapters.ntfy import NtfyNotificationSink
from synthorg.notifications.adapters.slack import SlackNotificationSink
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)

if TYPE_CHECKING:
    from synthorg.notifications.protocol import NotificationSink


@pytest.mark.unit
class TestSlackSinkLifecycle:
    """SlackNotificationSink lazy lifecycle."""

    def test_constructor_does_not_create_http_client(self) -> None:
        """``__init__`` is pure construction; no ``httpx.AsyncClient``."""
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        assert sink._client is None

    async def test_send_before_start_raises_runtime_error(self) -> None:
        """Calling ``send`` before ``start`` is a loud failure."""
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        n = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="Test",
            source="test",
        )
        with pytest.raises(RuntimeError, match="before start"):
            await sink.send(n)

    async def test_aenter_aexit_calls_aclose(self) -> None:
        """``async with`` closes the underlying client on exit."""
        aclose = AsyncMock()
        with patch(
            "synthorg.notifications.adapters.slack.httpx.AsyncClient",
            autospec=True,
        ) as mock_cls:
            mock_cls.return_value.aclose = aclose
            sink = SlackNotificationSink(
                webhook_url="https://hooks.example.com/services/abc",
            )
            async with sink:
                assert sink._client is mock_cls.return_value
            aclose.assert_awaited_once()
        assert sink._client is None

    async def test_double_start_is_idempotent(self) -> None:
        """Second ``start()`` does not create a second client."""
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        await sink.start()
        first = sink._client
        await sink.start()
        assert sink._client is first
        await sink.close()

    async def test_close_before_start_is_no_op(self) -> None:
        """``close()`` on a never-started sink is harmless."""
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        await sink.close()
        assert sink._client is None

    async def test_concurrent_start_creates_one_client(self) -> None:
        """Two ``start()`` coroutines converge on one client."""
        with patch(
            "synthorg.notifications.adapters.slack.httpx.AsyncClient",
            autospec=True,
        ) as mock_cls:
            sink = SlackNotificationSink(
                webhook_url="https://hooks.example.com/services/abc",
            )
            await asyncio.gather(sink.start(), sink.start())
            assert mock_cls.call_count == 1
            await sink.close()


@pytest.mark.unit
class TestNtfySinkLifecycle:
    """NtfyNotificationSink lazy lifecycle."""

    def test_constructor_does_not_create_http_client(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        assert sink._client is None

    async def test_send_before_start_raises_runtime_error(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        n = Notification(
            category=NotificationCategory.SYSTEM,
            severity=NotificationSeverity.INFO,
            title="Test",
            source="test",
        )
        with pytest.raises(RuntimeError, match="before start"):
            await sink.send(n)

    async def test_aenter_aexit_calls_aclose(self) -> None:
        aclose = AsyncMock()
        with patch(
            "synthorg.notifications.adapters.ntfy.httpx.AsyncClient",
            autospec=True,
        ) as mock_cls:
            mock_cls.return_value.aclose = aclose
            sink = NtfyNotificationSink(
                server_url="https://ntfy.example.com",
                topic="alerts",
            )
            async with sink:
                assert sink._client is mock_cls.return_value
            aclose.assert_awaited_once()
        assert sink._client is None

    async def test_double_start_is_idempotent(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        await sink.start()
        first = sink._client
        await sink.start()
        assert sink._client is first
        await sink.close()

    async def test_close_before_start_is_no_op(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        await sink.close()
        assert sink._client is None

    async def test_concurrent_start_creates_one_client(self) -> None:
        with patch(
            "synthorg.notifications.adapters.ntfy.httpx.AsyncClient",
            autospec=True,
        ) as mock_cls:
            sink = NtfyNotificationSink(
                server_url="https://ntfy.example.com",
                topic="alerts",
            )
            await asyncio.gather(sink.start(), sink.start())
            assert mock_cls.call_count == 1
            await sink.close()


class _RecordingSink:
    """Minimal sink that records lifecycle and send calls."""

    def __init__(self, *, name: str = "rec") -> None:
        self._name = name
        self.start_calls = 0
        self.close_calls = 0
        self.sends: list[Notification] = []

    @property
    def sink_name(self) -> str:
        return self._name

    async def send(self, notification: Notification) -> None:
        self.sends.append(notification)

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _StartFailingSink(_RecordingSink):
    """Sink whose ``start()`` always raises."""

    @override
    async def start(self) -> None:
        await super().start()
        msg = "boom"
        raise RuntimeError(msg)


class _CloseFailingSink(_RecordingSink):
    """Sink whose ``close()`` always raises."""

    @override
    async def close(self) -> None:
        await super().close()
        msg = "boom"
        raise RuntimeError(msg)


@pytest.mark.unit
class TestDispatcherLifecycle:
    """NotificationDispatcher start / aclose contract."""

    async def test_start_fans_out_to_all_sinks(self) -> None:
        a = _RecordingSink(name="a")
        b = _RecordingSink(name="b")
        c = _RecordingSink(name="c")
        d: list[NotificationSink] = [a, b, c]
        dispatcher = NotificationDispatcher(sinks=tuple(d))
        await dispatcher.start()
        assert a.start_calls == 1
        assert b.start_calls == 1
        assert c.start_calls == 1
        await dispatcher.aclose()

    async def test_aclose_fans_out_to_all_sinks(self) -> None:
        a = _RecordingSink(name="a")
        b = _RecordingSink(name="b")
        dispatcher = NotificationDispatcher(sinks=(a, b))
        await dispatcher.start()
        await dispatcher.aclose()
        assert a.close_calls == 1
        assert b.close_calls == 1

    async def test_failing_sink_dropped_from_active_set(self) -> None:
        """A sink whose ``start`` raises is removed from dispatch fan-out."""
        good = _RecordingSink(name="good")
        bad = _StartFailingSink(name="bad")
        dispatcher = NotificationDispatcher(sinks=(good, bad))
        await dispatcher.start()
        # The failing sink is dropped; ``bad`` is no longer in the set.
        assert bad not in dispatcher._sinks
        assert good in dispatcher._sinks
        await dispatcher.aclose()

    async def test_aclose_continues_after_one_sink_fails(self) -> None:
        """A failing ``close()`` does not abort the rest of the fan-out."""
        good = _RecordingSink(name="good")
        bad = _CloseFailingSink(name="bad")
        dispatcher = NotificationDispatcher(sinks=(good, bad))
        await dispatcher.start()
        # aclose must complete cleanly; both sinks see close().
        await dispatcher.aclose()
        assert good.close_calls == 1
        assert bad.close_calls == 1

    async def test_double_start_is_idempotent(self) -> None:
        a = _RecordingSink(name="a")
        dispatcher = NotificationDispatcher(sinks=(a,))
        await dispatcher.start()
        await dispatcher.start()
        assert a.start_calls == 1
        await dispatcher.aclose()

    async def test_aclose_before_start_is_no_op(self) -> None:
        a = _RecordingSink(name="a")
        dispatcher = NotificationDispatcher(sinks=(a,))
        await dispatcher.aclose()
        assert a.close_calls == 0

    async def test_dispatcher_close_alias_routes_through_aclose(self) -> None:
        """Legacy ``close()`` method has been removed; ``aclose`` is the API."""
        dispatcher = NotificationDispatcher(sinks=())
        # The canonical entry point is ``aclose``; ``close`` must not
        # exist as an alias (a permissive ``or callable`` check would
        # let a regression that re-introduces a no-op ``close`` slip
        # through).
        assert callable(dispatcher.aclose)
        assert not hasattr(dispatcher, "close")


@pytest.mark.unit
class TestSlackSendThroughLifecycle:
    """End-to-end smoke through ``async with`` to confirm send still works."""

    @respx.mock
    async def test_send_via_async_context_manager(self) -> None:
        route = respx.post("https://hooks.example.com/services/abc").mock(
            return_value=Response(200),
        )
        sink = SlackNotificationSink(
            webhook_url="https://hooks.example.com/services/abc",
        )
        async with sink:
            await sink.send(
                Notification(
                    category=NotificationCategory.SYSTEM,
                    severity=NotificationSeverity.INFO,
                    title="Hello",
                    source="test",
                ),
            )
        assert route.called

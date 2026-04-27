"""Tests for the ntfy notification sink."""

import httpx
import pytest
import respx
from httpx import Response

from synthorg.notifications.adapters.ntfy import NtfyNotificationSink
from synthorg.notifications.models import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
)


@pytest.mark.unit
class TestNtfyNotificationSink:
    def test_sink_name(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="test",
        )
        assert sink.sink_name == "ntfy"

    @respx.mock
    async def test_send_posts_to_correct_url(self) -> None:
        route = respx.post("https://ntfy.example.com/alerts").mock(
            return_value=Response(200),
        )
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="alerts",
        )
        async with sink:
            n = Notification(
                category=NotificationCategory.BUDGET,
                severity=NotificationSeverity.WARNING,
                title="Budget alert",
                source="test",
            )
            await sink.send(n)
        assert route.called
        request = route.calls.last.request
        assert request.headers["Title"] == "Budget alert"
        assert request.headers["Priority"] == "high"

    @respx.mock
    async def test_send_includes_auth_token(self) -> None:
        route = respx.post("https://ntfy.example.com/t").mock(
            return_value=Response(200),
        )
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com/",
            topic="t",
            token="tk_secret",
        )
        async with sink:
            n = Notification(
                category=NotificationCategory.SYSTEM,
                severity=NotificationSeverity.CRITICAL,
                title="Shutdown",
                source="test",
            )
            await sink.send(n)
        assert route.called
        request = route.calls.last.request
        assert request.headers["Authorization"] == "Bearer tk_secret"
        assert request.headers["Priority"] == "max"

    @respx.mock
    async def test_send_handles_server_error_gracefully(self) -> None:
        respx.post("https://ntfy.example.com/t").mock(
            return_value=Response(500),
        )
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="t",
        )
        async with sink:
            n = Notification(
                category=NotificationCategory.AGENT,
                severity=NotificationSeverity.INFO,
                title="Test",
                source="test",
            )
            # Adapter logs the error and re-raises for the dispatcher to track
            with pytest.raises(httpx.HTTPStatusError):
                await sink.send(n)

    async def test_close_before_any_send(self) -> None:
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="t",
        )
        # close() should succeed even if no send() was ever called
        await sink.close()

    @respx.mock
    async def test_close_after_send(self) -> None:
        respx.post("https://ntfy.example.com/t").mock(
            return_value=Response(200),
        )
        sink = NtfyNotificationSink(
            server_url="https://ntfy.example.com",
            topic="t",
        )
        await sink.start()
        try:
            n = Notification(
                category=NotificationCategory.SYSTEM,
                severity=NotificationSeverity.INFO,
                title="Close test",
                source="test",
            )
            await sink.send(n)
        finally:
            await sink.close()

    def test_rejects_loopback_url(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            NtfyNotificationSink(
                server_url="http://localhost:8080",
                topic="t",
            )

    def test_rejects_private_ip_url(self) -> None:
        with pytest.raises(ValueError, match="private"):
            NtfyNotificationSink(
                server_url="http://192.168.1.1:8080",
                topic="t",
            )

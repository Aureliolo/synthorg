"""Shared fixtures for communication tool tests."""

import pytest

from synthorg.notifications.models import Notification
from synthorg.tools.communication.config import (
    CommunicationToolsConfig,
    EmailConfig,
)


class MockNotificationDispatcher:
    """Mock notification dispatcher for testing."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        accepted: int = 1,
    ) -> None:
        self._error = error
        self._accepted = accepted
        self.dispatched: list[Notification] = []

    async def dispatch(self, notification: Notification) -> int:
        if self._error:
            raise self._error
        self.dispatched.append(notification)
        return self._accepted


@pytest.fixture
def email_config() -> EmailConfig:
    return EmailConfig(
        host="smtp.example.com",
        port=587,
        from_address="test@example.com",
    )


@pytest.fixture
def comm_config(email_config: EmailConfig) -> CommunicationToolsConfig:
    return CommunicationToolsConfig(email=email_config)


@pytest.fixture
def comm_config_no_email() -> CommunicationToolsConfig:
    return CommunicationToolsConfig()


@pytest.fixture
def mock_dispatcher() -> MockNotificationDispatcher:
    return MockNotificationDispatcher()


@pytest.fixture
def failing_dispatcher() -> MockNotificationDispatcher:
    return MockNotificationDispatcher(error=RuntimeError("dispatch failed"))


@pytest.fixture
def dropping_dispatcher() -> MockNotificationDispatcher:
    """A dispatcher that returns cleanly having delivered to nobody.

    Returns:
        The dispatcher, accepting the notification at no sink.
    """
    return MockNotificationDispatcher(accepted=0)

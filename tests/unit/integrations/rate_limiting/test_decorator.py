"""Tests for the tool-side rate-limiting decorator's own logging.

``with_connection_rate_limit`` fans out to three call sites; only one
(``external_api_tool.py``) catches ``ConnectionRateLimitError`` and logs its
own domain event. The other two would refuse silently at the default log
level with no signal at all, so the decorator itself logs a generic warning
before the error propagates.
"""

from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.integrations.errors import ConnectionRateLimitError
from synthorg.integrations.rate_limiting.decorator import with_connection_rate_limit
from synthorg.integrations.rate_limiting.shared_state import SharedRateLimitCoordinator
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class _FullWindowCoordinator:
    """A coordinator whose window is always full."""

    async def acquire(self) -> None:
        msg = "Rate limit exceeded for connection 'conn-a' (1 rpm)"
        raise ConnectionRateLimitError(msg)


async def test_a_full_connection_window_logs_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "synthorg.integrations.rate_limiting.shared_state.get_coordinator",
        lambda _connection_name: _FullWindowCoordinator(),
    )

    @with_connection_rate_limit("conn-a")
    async def call() -> str:
        return "should not run"

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ConnectionRateLimitError),
    ):
        await call()

    matches = [log for log in logs if log.get("event") == "integrations.rate_limit.hit"]
    assert len(matches) == 1
    assert matches[0]["log_level"] == "warning"
    assert matches[0]["connection_name"] == "conn-a"
    assert matches[0]["error_type"] == "ConnectionRateLimitError"


async def test_a_free_window_does_not_log_the_hit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = mock_of[SharedRateLimitCoordinator](acquire=AsyncMock())
    monkeypatch.setattr(
        "synthorg.integrations.rate_limiting.shared_state.get_coordinator",
        lambda _connection_name: coordinator,
    )

    @with_connection_rate_limit("conn-a")
    async def call() -> str:
        return "ran"

    with structlog.testing.capture_logs() as logs:
        result = await call()

    assert result == "ran"
    matches = [log for log in logs if log.get("event") == "integrations.rate_limit.hit"]
    assert matches == []

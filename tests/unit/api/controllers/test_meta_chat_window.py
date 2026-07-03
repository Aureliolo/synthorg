"""Tests for ``resolve_chat_snapshot_window``, the live chat-window resolver.

Covers the three paths ``test_meta_chat.py``'s
``test_snapshot_window_is_live_configurable`` doesn't reach: no resolver
wired, the resolver call raising, and the log-once guard resetting on
recovery.
"""

from collections.abc import Iterator
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

import synthorg.api.controllers._meta_chat_window as chat_window_module
from synthorg.api.controllers._meta_chat_window import (
    _DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS,
    resolve_chat_snapshot_window,
)
from synthorg.observability.events.meta import META_CHAT_DEPENDENCY_UNAVAILABLE
from synthorg.settings.resolver import ConfigResolver
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_fallback_logged_guard() -> Iterator[None]:
    """Reset the module-level log-once guard around every test.

    The guard is process-global (shared across tests in the same
    worker), so a prior test's failure path leaves it ``True`` and a
    later test asserting a fresh warning would silently see none.
    """
    chat_window_module._chat_window_fallback_logged = False
    yield
    chat_window_module._chat_window_fallback_logged = False


class TestResolveChatSnapshotWindow:
    async def test_no_resolver_wired_falls_back_to_default(self) -> None:
        state = make_app_state(config_resolver=None)

        result = await resolve_chat_snapshot_window(state)

        assert result == timedelta(days=_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS)

    async def test_resolver_raises_falls_back_and_logs_once(self) -> None:
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings outage"))
        )
        state = make_app_state(config_resolver=resolver)

        with capture_logs() as caplog:
            result = await resolve_chat_snapshot_window(state)

        assert result == timedelta(days=_DEFAULT_CHAT_SNAPSHOT_WINDOW_DAYS)
        events = [r.get("event") for r in caplog]
        assert events.count(META_CHAT_DEPENDENCY_UNAVAILABLE) == 1

    async def test_resolver_raises_repeatedly_logs_only_once(self) -> None:
        """A prolonged outage does not flood the logs on every turn."""
        resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings outage"))
        )
        state = make_app_state(config_resolver=resolver)

        with capture_logs() as caplog:
            await resolve_chat_snapshot_window(state)
            await resolve_chat_snapshot_window(state)
            await resolve_chat_snapshot_window(state)

        events = [r.get("event") for r in caplog]
        assert events.count(META_CHAT_DEPENDENCY_UNAVAILABLE) == 1

    async def test_recovery_after_failure_resets_the_guard(self) -> None:
        """A subsequent success clears the guard so a LATER failure warns again."""
        failing_resolver = mock_of[ConfigResolver](
            get_int=AsyncMock(side_effect=RuntimeError("settings outage"))
        )
        state = make_app_state(config_resolver=failing_resolver)
        await resolve_chat_snapshot_window(state)
        # A fresh local per phase, not a repeated module-attribute assert:
        # mypy/pyright narrow `chat_window_module._chat_window_fallback_logged`
        # to Literal[True] here and (wrongly) carry that narrowing across the
        # next `await`, which it can't see mutates the attribute via `global`,
        # making the later `is False` assert (and everything after it)
        # falsely "unreachable".
        guard_after_first_failure = chat_window_module._chat_window_fallback_logged
        assert guard_after_first_failure is True

        recovered_resolver = mock_of[ConfigResolver](get_int=AsyncMock(return_value=5))
        state = make_app_state(config_resolver=recovered_resolver)
        result = await resolve_chat_snapshot_window(state)
        assert result == timedelta(days=5)
        guard_after_recovery = chat_window_module._chat_window_fallback_logged
        assert guard_after_recovery is False

        # A second, independent failure after recovery must warn again --
        # proof the guard was genuinely reset, not left permanently tripped.
        state = make_app_state(config_resolver=failing_resolver)
        with capture_logs() as caplog:
            await resolve_chat_snapshot_window(state)
        events = [r.get("event") for r in caplog]
        assert events.count(META_CHAT_DEPENDENCY_UNAVAILABLE) == 1

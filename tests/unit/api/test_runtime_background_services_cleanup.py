"""Startup-failure cleanup for the runtime background services.

``_start_runtime_background_services`` keeps each individual service start
non-fatal, but if a *critical* propagates after some services already
started, the already-started ones must be stopped (bounded best-effort)
before the exception re-raises -- otherwise they leak, because
``on_shutdown`` never runs once ``on_startup`` fails.
"""

import pytest

from synthorg.api.lifecycle_runner_startup import _start_runtime_background_services
from synthorg.api.lifecycle_runner_support import _LifecycleTasks
from synthorg.integrations.state import IntegrationsStateSlice
from tests._shared import make_app_state

pytestmark = pytest.mark.unit


class _FakeService:
    """Async start/stop double recording its lifecycle calls."""

    def __init__(self, *, fail_start: Exception | None = None) -> None:
        self._fail_start = fail_start
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        if self._fail_start is not None:
            raise self._fail_start
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class TestStartRuntimeBackgroundServices:
    async def test_all_services_start_cleanly(self) -> None:
        bridge = _FakeService()
        prober = _FakeService()
        oauth = _FakeService()
        app_state = make_app_state(
            slices={
                IntegrationsStateSlice: {
                    "webhook_event_bridge": bridge,
                    "health_prober_service": prober,
                    "oauth_token_manager": oauth,
                }
            },
        )

        await _start_runtime_background_services(_LifecycleTasks(), app_state)
        assert bridge.started
        assert prober.started
        assert oauth.started
        assert not bridge.stopped
        assert not prober.stopped
        assert not oauth.stopped

    async def test_non_critical_start_failure_is_swallowed(self) -> None:
        # A poll-loop that cannot start must not abort boot: the failure is
        # logged and swallowed, and the later services still start.
        bridge = _FakeService(fail_start=ValueError("bridge down"))
        oauth = _FakeService()
        app_state = make_app_state(
            slices={
                IntegrationsStateSlice: {
                    "webhook_event_bridge": bridge,
                    "oauth_token_manager": oauth,
                }
            },
        )

        await _start_runtime_background_services(_LifecycleTasks(), app_state)
        assert not bridge.started
        assert not bridge.stopped  # non-fatal: nothing to clean up
        assert oauth.started

    async def test_critical_stops_already_started_services(self) -> None:
        # The bridge starts, then the health prober's start raises a critical
        # (MemoryError). The critical propagates, but the already-started
        # bridge must be stopped by the bounded cleanup before it does.
        bridge = _FakeService()
        prober = _FakeService(fail_start=MemoryError("oom during start"))
        oauth = _FakeService()
        app_state = make_app_state(
            slices={
                IntegrationsStateSlice: {
                    "webhook_event_bridge": bridge,
                    "health_prober_service": prober,
                    "oauth_token_manager": oauth,
                }
            },
        )

        with pytest.raises(MemoryError):
            await _start_runtime_background_services(_LifecycleTasks(), app_state)
        # Earlier-started service cleaned up; the one that failed mid-start and
        # the one never reached are not stopped.
        assert bridge.started
        assert bridge.stopped
        assert not prober.stopped
        assert not oauth.started
        assert not oauth.stopped

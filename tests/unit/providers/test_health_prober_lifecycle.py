"""Lifecycle tests for ``ProviderHealthProber``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):

* concurrent ``start()`` calls spawn at most one background task,
* a re-``start()`` after ``stop()`` works,
* a ``stop()`` whose drain exceeds the hard deadline marks the
  prober unrestartable so the next ``start()`` refuses.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.config.schema import ProviderConfig
from synthorg.providers.errors import ProviderLifecycleConflictError
from synthorg.providers.health import ProviderHealthTracker
from synthorg.providers.health_prober import ProviderHealthProber
from synthorg.settings.resolver import ConfigResolver

pytestmark = pytest.mark.unit


def _make_prober() -> ProviderHealthProber:
    config = MagicMock(spec=ProviderConfig)
    config.base_url = "http://localhost:11434"
    config.litellm_provider = "ollama"
    config.auth_type = "none"
    config.api_key = None
    resolver = MagicMock(spec=ConfigResolver)
    resolver.get_provider_configs = AsyncMock(
        spec=ConfigResolver.get_provider_configs,
        return_value={"test-local": config},
    )
    resolver.get_int = AsyncMock(spec=ConfigResolver.get_int, return_value=11434)
    return ProviderHealthProber(
        ProviderHealthTracker(),
        resolver,
        discovery_policy_loader=None,
        interval_seconds=3600,
    )


class TestProviderHealthProberLifecycle:
    """Canonical lifecycle pattern."""

    async def test_concurrent_starts_spawn_one_task(self) -> None:
        prober = _make_prober()
        try:
            await asyncio.gather(
                prober.start(),
                prober.start(),
                prober.start(),
            )
            assert prober._task is not None
            task = prober._task
            await asyncio.gather(
                prober.start(),
                prober.start(),
            )
            assert prober._task is task
        finally:
            await prober.stop()

    async def test_restart_after_clean_stop(self) -> None:
        prober = _make_prober()
        await prober.start()
        await prober.stop()
        # After a clean stop the prober must restart.
        await prober.start()
        await prober.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A drain that exceeds the deadline marks the service unrestartable."""
        prober = _make_prober()
        prober._stop_drain_timeout_seconds = 0.05
        # Replace _run_loop with a coroutine that swallows cancellation
        # so the drain hangs and triggers the timeout path. ``release``
        # lets the test wake the patched loop after the timeout
        # assertion so the orphan task drains deterministically
        # instead of leaking past the patch scope and racing the next
        # test.
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: ProviderHealthProber) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Suppress cancellation; this simulates a stuck drain.
                await release.wait()

        with patch.object(ProviderHealthProber, "_run_loop", hung_loop):
            await prober.start()
            await entered.wait()
            saved_task = prober._task
            try:
                with pytest.raises(TimeoutError):
                    await prober.stop()
                assert prober._stop_failed is True
                assert saved_task is not None
            finally:
                release.set()
                if saved_task is not None:
                    await saved_task

        # Subsequent start must refuse.
        with pytest.raises(ProviderLifecycleConflictError, match="unrestartable"):
            await prober.start()

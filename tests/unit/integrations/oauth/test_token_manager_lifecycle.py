"""Lifecycle tests for ``OAuthTokenManager``.

Verifies the canonical pattern (per ``docs/reference/lifecycle-sync.md``):
a re-``start()`` after a clean ``stop()`` works, and a ``stop()`` whose
drain exceeds the hard deadline marks the manager unrestartable so the
next ``start()`` refuses.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.errors import IntegrationLifecycleConflictError
from synthorg.integrations.oauth.token_manager import OAuthTokenManager

pytestmark = pytest.mark.unit


def _make_manager() -> OAuthTokenManager:
    catalog = MagicMock(spec=ConnectionCatalog)
    return OAuthTokenManager(catalog)


class TestOAuthTokenManagerLifecycle:
    """Canonical lifecycle pattern."""

    async def test_restart_after_clean_stop(self) -> None:
        manager = _make_manager()
        await manager.start()
        await manager.stop()
        # After a clean stop the manager must restart.
        await manager.start()
        await manager.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        """A drain that exceeds the deadline marks the manager unrestartable."""
        manager = _make_manager()
        manager._stop_drain_timeout_seconds = 0.05
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: OAuthTokenManager) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with patch.object(OAuthTokenManager, "_refresh_loop", hung_loop):
            await manager.start()
            await entered.wait()
            saved_task = manager._task
            try:
                with pytest.raises(TimeoutError):
                    await manager.stop()
                assert manager._stop_failed is True
                assert saved_task is not None
            finally:
                release.set()
                if saved_task is not None:
                    await saved_task

        with pytest.raises(IntegrationLifecycleConflictError, match="unrestartable"):
            await manager.start()

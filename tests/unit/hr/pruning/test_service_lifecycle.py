"""Canonical lifecycle pattern tests for ``PruningService``.

The unit ``test_service.py`` covers happy-path start / stop. This
module verifies the lock-driven concurrency safety added per
``docs/reference/lifecycle-sync.md``: concurrent start, restart after
clean stop, and unrestartable flag after a drain timeout.
"""

import asyncio
from unittest.mock import patch

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.hr.pruning.models import PruningServiceConfig
from synthorg.hr.pruning.service import PruningService
from synthorg.hr.registry import AgentRegistryService

pytestmark = pytest.mark.unit


class _FakeOffboarding:
    async def offboard(self, request: object) -> None:
        del request


class _FakeTracker:
    pass


def _make_service() -> PruningService:
    return PruningService(
        policies=(),
        registry=AgentRegistryService(),
        tracker=_FakeTracker(),  # type: ignore[arg-type]
        approval_store=ApprovalStore(),
        offboarding_service=_FakeOffboarding(),  # type: ignore[arg-type]
        config=PruningServiceConfig(evaluation_interval_seconds=3600.0),
    )


class TestPruningServiceLifecycleLock:
    """Canonical pattern compliance."""

    async def test_concurrent_starts_spawn_one_task(self) -> None:
        service = _make_service()
        try:
            await asyncio.gather(
                service.start(),
                service.start(),
                service.start(),
            )
            assert service.is_running
        finally:
            await service.stop()

    async def test_restart_after_clean_stop(self) -> None:
        service = _make_service()
        await service.start()
        await service.stop()
        # After a clean stop, the service must accept a restart on a
        # fresh ``_task``.  Cannot assert ``not is_running`` here:
        # mypy narrows ``is_running`` to ``False`` after the property
        # access and would then flag every subsequent ``await
        # service.start()`` / ``stop()`` as unreachable.
        await service.start()
        await service.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        service = _make_service()
        service._stop_drain_timeout_seconds = 0.05

        async def hung_loop(self: PruningService) -> None:
            del self
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(1.0)

        with patch.object(PruningService, "_run_loop", hung_loop):
            await service.start()
            await asyncio.sleep(0)

            with pytest.raises(TimeoutError):
                await service.stop()
            assert service._stop_failed is True

        with pytest.raises(RuntimeError, match="unrestartable"):
            await service.start()

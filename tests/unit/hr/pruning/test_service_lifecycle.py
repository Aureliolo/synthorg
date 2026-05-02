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
        # Patch ``asyncio.create_task`` (as resolved through the
        # service module so the patch reaches the call site) to count
        # spawn invocations: ``service.is_running`` alone would also
        # be true if the lock leaked and three loop tasks raced. The
        # canonical lifecycle contract is "exactly one spawn under
        # concurrent ``start()`` callers", which we now assert
        # directly.
        original_create_task = asyncio.create_task
        spawned: list[asyncio.Task[object]] = []

        def _counting_create_task(
            coro: object,
            **kwargs: object,
        ) -> asyncio.Task[object]:
            task: asyncio.Task[object] = original_create_task(coro, **kwargs)  # type: ignore[arg-type]
            spawned.append(task)
            return task

        try:
            with patch(
                "synthorg.hr.pruning.service.asyncio.create_task",
                _counting_create_task,
            ):
                await asyncio.gather(
                    service.start(),
                    service.start(),
                    service.start(),
                )
            assert service.is_running
            assert len(spawned) == 1
        finally:
            await service.stop()

    async def test_restart_after_clean_stop(self) -> None:
        service = _make_service()
        await service.start()
        await service.stop()
        # After a clean stop, the service must accept a restart on a
        # fresh ``_task``. Cannot assert ``not is_running`` between
        # the calls because mypy narrows ``is_running`` to ``False``
        # after the property access and would then flag every
        # subsequent ``await service.start()`` / ``stop()`` as
        # unreachable.
        await service.start()
        # Positive assertion proves the second ``start()`` actually
        # took effect: without this, a regression where ``start()``
        # silently no-ops after a stop would still pass the test.
        assert service.is_running
        await service.stop()

    async def test_unrestartable_after_drain_timeout(self) -> None:
        service = _make_service()
        service._stop_drain_timeout_seconds = 0.05
        # ``release`` lets the test wake the patched loop after the
        # timeout assertion. Without it, the suppressed-cancel branch
        # would block on a wall-clock sleep and leak a pending task
        # past the patch scope; later tests could then observe a
        # ``_task`` that does not belong to them.
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hung_loop(self: PruningService) -> None:
            del self
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        with patch.object(PruningService, "_run_loop", hung_loop):
            await service.start()
            await entered.wait()
            saved_task = service._task
            try:
                with pytest.raises(TimeoutError):
                    await service.stop()
                assert service._stop_failed is True
                assert saved_task is not None
            finally:
                # ``finally`` so a failed assertion above still
                # releases the hung loop and drains the orphan task,
                # rather than leaking it into the next test run.
                release.set()
                if saved_task is not None:
                    await saved_task

        with pytest.raises(RuntimeError, match="unrestartable"):
            await service.start()

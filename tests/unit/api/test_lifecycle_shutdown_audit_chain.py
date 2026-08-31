"""Shutdown stops the audit-chain verification scheduler.

Left running past shutdown, the scheduler's background task would keep
firing verify cycles against a sink whose durable writer is about to be
(or already has been) detached, and the task itself would leak past the
lifespan -- the same class of leak ``test_lifecycle_runner_threading.py``
locks for the three janitor loops.
"""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.config.schema import RootConfig
from synthorg.core.scheduler import AsyncCycleScheduler
from synthorg.observability.state import ObservabilityStateSlice
from tests._shared import make_app_state
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit


class _StubScheduler(AsyncCycleScheduler):
    """A scheduler whose cycle body does nothing, so start()/stop() are the
    only behaviour under test -- not the real audit-chain verify walk."""

    def __init__(self) -> None:
        super().__init__(
            interval_seconds=3600.0,
            task_name="audit-chain-verify-stub",
            started_event="test.stub.started",
            stopped_event="test.stub.stopped",
            failed_event="test.stub.failed",
        )

    @override
    async def _run_cycle_once(self) -> None:
        return None


class TestAuditChainSchedulerStoppedOnShutdown:
    async def _shutdown_with(self, scheduler: AsyncCycleScheduler | None) -> None:
        """Run a full lifespan with *scheduler* published on the slice."""
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app_state = make_app_state(
            config=RootConfig(company_name="audit-chain-shutdown"),
            approval_store=ApprovalStore(),
            persistence=persistence,
        )
        startup, shutdown = _build_lifecycle(
            persistence=persistence,
            message_bus=None,
            bridge=None,
            settings_dispatcher=None,
            task_engine=None,
            backup_service=None,
            approval_timeout_scheduler=None,
            app_state=app_state,
        )
        try:
            await startup[0]()
            if scheduler is not None:
                await scheduler.start()
                app_state.swap_slice(
                    app_state.slice(ObservabilityStateSlice).model_copy(
                        update={"audit_chain_verify_scheduler": scheduler}
                    )
                )
            await shutdown[0]()
        finally:
            await persistence.disconnect()

    async def test_a_running_scheduler_is_stopped(self) -> None:
        scheduler = _StubScheduler()

        await self._shutdown_with(scheduler)

        assert scheduler.is_running is False

    async def test_no_scheduler_wired_is_not_a_failure(self) -> None:
        """A deployment with audit_chain.enabled=False wires none."""
        await self._shutdown_with(None)

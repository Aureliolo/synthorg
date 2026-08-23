"""Shutdown reaches the fine-tune orchestrator.

A fine-tune run is a background task driving a worker thread through hours of
training, and no other teardown step touches it. Left alone at SIGTERM it runs
on until the orchestrator's SIGKILL, so the checkpoint it had not yet written
is lost and the run's own outcome is never recorded.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.config.schema import RootConfig
from synthorg.memory.embedding.fine_tune_orchestrator import FineTuneOrchestrator
from synthorg.memory.state import MemoryStateSlice
from tests._shared import make_app_state, mock_of
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit


@pytest.mark.unit
class TestFineTuneCancelledOnShutdown:
    """The in-flight run is cancelled before the memory teardown around it."""

    async def _shutdown_with(self, orchestrator: object | None) -> None:
        """Run a full lifespan with *orchestrator* published on the slice."""
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app_state = make_app_state(
            config=RootConfig(company_name="fine-tune-shutdown"),
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
            app_state.swap_slice(
                app_state.slice(MemoryStateSlice).model_copy(
                    update={"fine_tune_orchestrator": orchestrator}
                )
            )
            await shutdown[0]()
        finally:
            await persistence.disconnect()

    async def test_the_run_is_cancelled(self) -> None:
        orchestrator = mock_of[FineTuneOrchestrator](
            cancel=AsyncMock(spec=FineTuneOrchestrator.cancel)
        )

        await self._shutdown_with(orchestrator)

        orchestrator.cancel.assert_awaited_once()

    async def test_a_failing_cancel_does_not_abort_the_rest_of_shutdown(self) -> None:
        """Every sibling teardown step still has to run."""
        error = RuntimeError("training thread wedged")
        orchestrator = mock_of[FineTuneOrchestrator](
            cancel=AsyncMock(spec=FineTuneOrchestrator.cancel, side_effect=error)
        )

        await self._shutdown_with(orchestrator)

        orchestrator.cancel.assert_awaited_once()

    async def test_no_orchestrator_wired_is_not_a_failure(self) -> None:
        """A deployment whose persistence has no fine-tune tables wires none."""
        await self._shutdown_with(None)

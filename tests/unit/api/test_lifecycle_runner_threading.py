"""The shared ``_LifecycleTasks`` is threaded between the two runners.

The on-startup / on-shutdown closures were lifted out of ``_build_lifecycle``
into top-level ``_run_startup`` / ``_run_shutdown`` functions; their former
``nonlocal`` janitor-task state moved onto a shared ``_LifecycleTasks``
container the builder threads into both. This test locks the threading: a full
startup spawns the three named janitor loops, and a full shutdown cancels the
*same* tasks. Were the container not shared (each runner seeing its own), the
shutdown would see ``None`` handles and the janitor loops would leak past the
lifespan -- which this test fails on.
"""

import asyncio

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_builder import _build_lifecycle
from synthorg.config.schema import RootConfig
from tests._shared import make_app_state
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit

_JANITOR_TASK_NAMES = frozenset(
    {"ws-ticket-cleanup", "audit-retention", "webhook-receipt-cleanup"}
)


@pytest.mark.unit
class TestLifecycleTasksThreading:
    """on_startup populates and on_shutdown drains the shared task container."""

    async def test_startup_spawns_then_shutdown_cancels_janitor_tasks(self) -> None:
        """The three janitor loops spawn on startup and are gone after shutdown."""
        persistence = FakePersistenceBackend()
        await persistence.connect()
        app_state = make_app_state(
            config=RootConfig(company_name="lifecycle-threading"),
            approval_store=ApprovalStore(),
            persistence=persistence,
        )
        startup, shutdown = _build_lifecycle(
            persistence,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            app_state,
        )
        try:
            await startup[0]()

            live_after_startup = {
                task.get_name() for task in asyncio.all_tasks() if not task.done()
            }
            assert live_after_startup >= _JANITOR_TASK_NAMES, (
                f"startup should spawn every janitor loop; live tasks: "
                f"{sorted(live_after_startup)}"
            )

            await shutdown[0]()

            live_after_shutdown = {
                task.get_name() for task in asyncio.all_tasks() if not task.done()
            }
            leaked = _JANITOR_TASK_NAMES & live_after_shutdown
            assert not leaked, (
                f"shutdown must cancel the same janitor tasks startup spawned; "
                f"leaked: {sorted(leaked)}"
            )
        finally:
            await persistence.disconnect()

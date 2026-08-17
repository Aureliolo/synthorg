"""The activity feed hands out names, never the keys they stand for.

A row stores references because a stored name goes stale the moment an agent is
renamed or a task retitled. The names are resolved at the read boundary, and the
descriptions carry no identifier at all: they used to read
``Task 847b6f0e-2b49-46fc-a783-852fa... produced no artifacts``, so an operator
watching the feed was shown a UUID for every run.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, TaskType
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.performance.tracker import PerformanceTracker
from tests._shared import FakeClock, LoopAsyncClient
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)
_TASK_ID = "3f2a4b1c-0000-4000-8000-00000000abcd"
_AGENT_ID = "00000000-0000-0000-0000-000000000aaa"

#: Any 8-4-4-4-12 hex run. What an operator must never be shown.
_UUID_SHAPED = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def _freeze_controller_time(
    async_test_client: LoopAsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the controller's clock so the window covers the seeded rows."""
    app_state = async_test_client.app.state.app_state
    monkeypatch.setattr(app_state, "clock", FakeClock(start=_NOW))


def _task_metric() -> TaskMetricRecord:
    return TaskMetricRecord(
        agent_id=_AGENT_ID,
        task_id=_TASK_ID,
        task_type=TaskType.DEVELOPMENT,
        completed_at=_NOW - timedelta(hours=1),
        is_success=True,
        duration_seconds=10.0,
        cost=0.01,
        currency="EUR",
        complexity=Complexity.SIMPLE,
    )


class TestActivityFeedNaming:
    async def test_description_carries_no_identifier(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
    ) -> None:
        await performance_tracker.record_task_metric(_task_metric())

        resp = await async_test_client.get("/api/v1/activities")

        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert _UUID_SHAPED.search(row["description"]) is None
        assert _TASK_ID not in row["description"]
        # The reference itself still travels, which is what the surface links by.
        assert row["related_ids"]["task_id"] == _TASK_ID

    async def test_subject_title_names_the_task(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.tasks.save(
            Task(
                id=UUID(_TASK_ID),
                title="Wire the login page",
                description="d",
                type=TaskType.DEVELOPMENT,
                project="p",
                created_by="c",
            )
        )
        await performance_tracker.record_task_metric(_task_metric())

        resp = await async_test_client.get("/api/v1/activities")

        assert resp.json()["data"][0]["subject_title"] == "Wire the login page"

    async def test_unresolvable_task_leaves_the_title_unset(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
    ) -> None:
        """A deleted task names nothing, and the surface must word that itself.

        Never the key: a fallback that reached for ``task_id`` would put the UUID
        straight back on screen, which is the whole defect.
        """
        await performance_tracker.record_task_metric(_task_metric())

        resp = await async_test_client.get("/api/v1/activities")

        assert resp.json()["data"][0]["subject_title"] is None

    async def test_unresolvable_actor_leaves_the_name_unset(
        self,
        async_test_client: LoopAsyncClient,
        performance_tracker: PerformanceTracker,
    ) -> None:
        await performance_tracker.record_task_metric(_task_metric())

        resp = await async_test_client.get("/api/v1/activities")

        row = resp.json()["data"][0]
        assert row["actor_name"] is None
        assert row["related_ids"]["agent_id"] == _AGENT_ID

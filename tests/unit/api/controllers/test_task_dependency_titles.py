"""A task's dependencies reach the detail surface as titles, never as keys.

The list is what an operator acts on ("this is waiting on the login page"), and
a column of UUIDs answers nothing. The titles are resolved once per response at
the read boundary; a dependency nothing resolves is simply absent from the map,
and the surface then says so in its own words rather than printing the key.
"""

from uuid import UUID

import pytest

from synthorg.api.dto_named_rows import TaskRow
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import FakePersistenceBackend

pytestmark = pytest.mark.unit

_SUBJECT = sid("dependency-titles-subject")
_RESOLVED = sid("dependency-titles-resolved")
_GONE = sid("dependency-titles-gone")


def _task(task_id: str, title: str, *, dependencies: tuple[str, ...] = ()) -> Task:
    """Build a task with the given dependencies.

    Returns:
        The task.
    """
    return Task(
        id=UUID(task_id),
        title=title,
        description="d",
        type=TaskType.DEVELOPMENT,
        project="p",
        created_by="c",
        dependencies=dependencies,
    )


class TestTheDetailReadTitlesEveryDependency:
    """One read for the whole list, and an absent title stays absent."""

    async def test_a_resolvable_dependency_is_titled(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.tasks.save(_task(_RESOLVED, "Wire the login page"))
        await fake_persistence.tasks.save(
            _task(_SUBJECT, "Ship it", dependencies=(_RESOLVED,))
        )

        resp = await async_test_client.get(f"/api/v1/tasks/{_SUBJECT}")

        assert resp.status_code == 200
        assert resp.json()["data"]["dependency_titles"] == {
            _RESOLVED: "Wire the login page"
        }

    async def test_an_unresolvable_dependency_is_omitted_not_keyed(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Absent, not present-with-the-id: the surface supplies its own words."""
        await fake_persistence.tasks.save(_task(_RESOLVED, "Wire the login page"))
        await fake_persistence.tasks.save(
            _task(_SUBJECT, "Ship it", dependencies=(_RESOLVED, _GONE))
        )

        resp = await async_test_client.get(f"/api/v1/tasks/{_SUBJECT}")

        titles = resp.json()["data"]["dependency_titles"]
        assert titles == {_RESOLVED: "Wire the login page"}
        assert _GONE not in titles
        # The reference itself still travels: the surface links by it.
        assert _GONE in resp.json()["data"]["dependencies"]

    async def test_a_task_with_no_dependencies_carries_an_empty_map(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        await fake_persistence.tasks.save(_task(_SUBJECT, "Ship it"))

        resp = await async_test_client.get(f"/api/v1/tasks/{_SUBJECT}")

        assert resp.json()["data"]["dependency_titles"] == {}


class TestTheMapCannotBeMutatedThroughTheFrozenRow:
    """`frozen=True` stops the field being reassigned and not the dict behind it."""

    def test_the_caller_s_mapping_is_deep_copied(self) -> None:
        supplied = {"dep-1": "Wire the login page"}
        row = TaskRow.of(
            _task(_SUBJECT, "Ship it", dependencies=("dep-1",)),
            {},
            supplied,
        )

        supplied["dep-1"] = "Something else entirely"

        assert row.dependency_titles == {"dep-1": "Wire the login page"}

"""The reclamation sweep: a held container is released once its run is over.

Grounded in the task table rather than in the tracked-container rows, because
mid-process "no row" means "being created" as often as "orphaned". Asserted on
doubles: which owners it releases, which it keeps, and what it does with a key
it cannot read.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.tools.sandbox.lifecycle.config import LifecycleStrategy
from synthorg.tools.sandbox.lifecycle.protocol import TrackedOwner
from synthorg.tools.sandbox.reclaim import (
    RUNNING_STATUSES,
    ReclaimableSandbox,
    SandboxOwnerReclaimer,
    parse_owner_key,
)
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_TASK = sid("task:reclaim")
_AGENT = sid("agent:reclaim")
_GENERATION = 7


def _task(status: TaskStatus) -> Task:
    """A task in *status*, assigned to the agent under test.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("task:reclaim"),
        title=NotBlankStr("Build it"),
        description=NotBlankStr("Build the thing."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr("proj"),
        created_by=NotBlankStr("test"),
        status=status,
        assigned_to=NotBlankStr(_AGENT),
    )


def _backend(*keys: str) -> tuple[ReclaimableSandbox, AsyncMock]:
    """A backend holding *keys*, with the release mock beside it.

    Returns:
        The backend and its release mock.
    """
    release = AsyncMock()
    tracked = tuple(
        TrackedOwner(key=NotBlankStr(key), generation=_GENERATION) for key in keys
    )
    backend: ReclaimableSandbox = mock_of[ReclaimableSandbox](
        tracked_owners=AsyncMock(return_value=tracked), release_key=release
    )
    return backend, release


class TestReadingAKeyBack:
    """The inverse of the key the lifecycle built."""

    def test_a_fully_qualified_key_reads_back_into_its_parts(self) -> None:
        parsed = parse_owner_key(f"proj:{_TASK}:img-0123456789ab:rw")

        assert parsed is not None
        assert parsed.project_id == "proj"
        assert parsed.owner == _TASK
        assert parsed.image_segment == "img-0123456789ab"
        assert parsed.mount_mode == "rw"

    def test_a_project_carrying_a_colon_keeps_it(self) -> None:
        parsed = parse_owner_key(f"org:team:{_TASK}:ro")

        assert parsed is not None
        assert parsed.project_id == "org:team"
        assert parsed.owner == _TASK

    def test_an_unprefixed_key_has_no_project(self) -> None:
        parsed = parse_owner_key(_TASK)

        assert parsed is not None
        assert parsed.project_id is None
        assert parsed.owner == _TASK
        assert parsed.image_segment is None
        assert parsed.mount_mode is None

    def test_a_key_with_no_owner_is_refused(self) -> None:
        assert parse_owner_key("proj:") is None
        assert parse_owner_key("") is None

    def test_a_project_shaped_like_an_image_segment_is_not_one(self) -> None:
        # The image segment is recognised by its position at the END of the
        # key, so a project id merely containing the shape keeps its text.
        parsed = parse_owner_key(f"myimg-0123456789ab:{_TASK}:rw")

        assert parsed is not None
        assert parsed.project_id == "myimg-0123456789ab"
        assert parsed.owner == _TASK
        assert parsed.image_segment is None


class TestPerTaskGrounding:
    """A task's container goes when the task stops running."""

    @pytest.mark.parametrize("status", sorted(RUNNING_STATUSES, key=str))
    async def test_a_running_task_keeps_its_container(self, status: TaskStatus) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw")
        tasks = mock_of[TaskRepository](get=AsyncMock(return_value=_task(status)))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.kept == (f"proj:{_TASK}:rw",)
        release.assert_not_awaited()

    @pytest.mark.parametrize(
        "status", [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED]
    )
    async def test_a_finished_task_releases_its_container(
        self, status: TaskStatus
    ) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw")
        tasks = mock_of[TaskRepository](get=AsyncMock(return_value=_task(status)))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.released == (f"proj:{_TASK}:rw",)
        # The generation the key was READ under travels with the release, so
        # the lifecycle can refuse it once the key has been acquired again.
        release.assert_awaited_once_with(f"proj:{_TASK}:rw", generation=_GENERATION)

    async def test_a_task_that_no_longer_exists_releases_its_container(
        self,
    ) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw")
        tasks = mock_of[TaskRepository](get=AsyncMock(return_value=None))

        await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        release.assert_awaited_once()

    async def test_every_mount_mode_of_one_owner_is_released(self) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw", f"proj:{_TASK}:ro")
        tasks = mock_of[TaskRepository](
            get=AsyncMock(return_value=_task(TaskStatus.COMPLETED))
        )

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert len(outcome.released) == 2
        assert release.await_count == 2


class TestPerAgentGrounding:
    """An agent's container goes when no task of theirs is running."""

    async def test_an_agent_with_a_running_task_keeps_its_container(self) -> None:
        backend, release = _backend(f"proj:{_AGENT}:rw")
        tasks = mock_of[TaskRepository](
            query=AsyncMock(return_value=(_task(TaskStatus.IN_PROGRESS),))
        )

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_AGENT, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.kept == (f"proj:{_AGENT}:rw",)
        release.assert_not_awaited()

    async def test_an_agent_with_no_running_task_releases_its_container(
        self,
    ) -> None:
        backend, release = _backend(f"proj:{_AGENT}:rw")
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=()))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_AGENT, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.released == (f"proj:{_AGENT}:rw",)
        release.assert_awaited_once()
        # One probe per running status, each asking for the agent by id.
        assert tasks.query.await_count == len(RUNNING_STATUSES)


class TestWhatTheSweepRefusesToDecide:
    """Unreadable, unreachable or unknown: kept, and said."""

    async def test_a_key_it_cannot_read_is_left_alone(self) -> None:
        backend, release = _backend("proj:")
        tasks = mock_of[TaskRepository]()

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.unparseable == ("proj:",)
        release.assert_not_awaited()

    async def test_a_grounding_read_that_fails_keeps_the_container(self) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw")
        tasks = mock_of[TaskRepository](get=AsyncMock(side_effect=OSError("down")))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.kept == (f"proj:{_TASK}:rw",)
        release.assert_not_awaited()

    async def test_a_failed_release_keeps_the_key_for_the_next_pass(self) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw", f"proj:{sid('t2')}:rw")
        release.side_effect = [OSError("daemon"), None]
        tasks = mock_of[TaskRepository](get=AsyncMock(return_value=None))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_TASK, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.kept == (f"proj:{_TASK}:rw",)
        assert outcome.released == (f"proj:{sid('t2')}:rw",)

    async def test_a_lifecycle_it_cannot_attribute_releases_nothing(self) -> None:
        backend, release = _backend(f"proj:{_TASK}:rw")
        tasks = mock_of[TaskRepository](get=AsyncMock(return_value=None))

        outcome = await SandboxOwnerReclaimer(
            backend=backend, strategy_kind=LifecycleStrategy.PER_CALL, tasks=tasks
        ).reconcile(trigger="test")

        assert outcome.kept == (f"proj:{_TASK}:rw",)
        release.assert_not_awaited()

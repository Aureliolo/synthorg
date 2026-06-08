"""Unit tests for :class:`DistributedDispatcher`.

Uses a fake task queue (recording published claims) so the tests
exercise the dispatcher's filter + claim-building logic without
requiring a live NATS container.
"""

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.settings.bridge_configs import WorkersBridgeConfig
from synthorg.workers.claim import TaskClaim
from synthorg.workers.dispatcher import DistributedDispatcher
from tests._shared import FakeClock, as_uuid, sid


class _FakeTaskQueue:
    """Minimal stand-in for :class:`JetStreamTaskQueue`."""

    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.published: list[TaskClaim] = []
        self.publish_error: Exception | None = None

    @property
    def is_running(self) -> bool:
        return self.running

    async def publish_claim(self, claim: TaskClaim) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append(claim)


def _make_task(status: TaskStatus = TaskStatus.ASSIGNED) -> Task:
    """Build a minimal Task for event fixtures.

    Most statuses require ``assigned_to`` (enforced by a Task
    validator), so we set it unconditionally for the test double.
    """
    return Task(
        id=as_uuid("task-1"),
        title="Test task",
        description="Integration fixture for dispatcher tests.",
        type=TaskType.DEVELOPMENT,
        project="project-1",
        priority=Priority.MEDIUM,
        status=status,
        assigned_to="agent-1",
        created_by="engine",
    )


def _make_event(
    *,
    new_status: TaskStatus,
    previous_status: TaskStatus | None = TaskStatus.CREATED,
    task: Task | None = None,
) -> TaskStateChanged:
    return TaskStateChanged(
        mutation_type="transition",
        request_id="req-1",
        requested_by="engine",
        task_id=sid("task-1"),
        task=task or _make_task(status=new_status),
        previous_status=previous_status,
        new_status=new_status,
        version=1,
        reason="ready to run",
    )


@pytest.mark.unit
async def test_dispatcher_publishes_claim_on_ready_transition() -> None:
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = _make_event(new_status=TaskStatus.ASSIGNED)
    await dispatcher.on_task_state_changed(event)

    assert len(queue.published) == 1
    claim = queue.published[0]
    assert claim.task_id == sid("task-1")
    assert claim.new_status == "assigned"
    assert claim.previous_status == "created"
    assert claim.project_id == "project-1"


@pytest.mark.unit
async def test_dispatcher_ignores_non_dispatchable_transitions() -> None:
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = _make_event(
        new_status=TaskStatus.COMPLETED,
        previous_status=TaskStatus.IN_PROGRESS,
    )
    await dispatcher.on_task_state_changed(event)

    assert queue.published == []


@pytest.mark.unit
async def test_dispatcher_ignores_idempotent_assigned_updates() -> None:
    """Re-emitting an event whose task is already assigned must not re-dispatch.

    Without a ``previous_status != new_status`` guard the dispatcher
    would double-enqueue whenever a downstream observer replays a
    ``TaskStateChanged`` event (e.g. metadata edit on an already-
    assigned task). Regression guard for PR #1214 review feedback.
    """
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = _make_event(
        new_status=TaskStatus.ASSIGNED,
        previous_status=TaskStatus.ASSIGNED,
    )
    await dispatcher.on_task_state_changed(event)

    assert queue.published == []


@pytest.mark.unit
async def test_dispatcher_skips_when_queue_not_running() -> None:
    queue = _FakeTaskQueue(running=False)
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = _make_event(new_status=TaskStatus.ASSIGNED)
    await dispatcher.on_task_state_changed(event)

    assert queue.published == []


@pytest.mark.unit
async def test_dispatcher_swallows_publish_errors() -> None:
    queue = _FakeTaskQueue()
    queue.publish_error = RuntimeError("NATS unavailable")
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = _make_event(new_status=TaskStatus.ASSIGNED)
    # Should not propagate; dispatcher is best-effort.
    await dispatcher.on_task_state_changed(event)
    assert queue.published == []


@pytest.mark.unit
async def test_dispatcher_builds_claim_with_task_snapshot() -> None:
    """Transitions that carry a task snapshot populate ``project_id``."""
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = TaskStateChanged(
        mutation_type="create",
        request_id="req-1",
        requested_by="engine",
        task_id=sid("task-1"),
        task=_make_task(status=TaskStatus.ASSIGNED),
        previous_status=None,
        new_status=TaskStatus.ASSIGNED,
        version=1,
    )
    await dispatcher.on_task_state_changed(event)

    assert len(queue.published) == 1
    assert queue.published[0].previous_status is None
    assert queue.published[0].project_id == "project-1"


@pytest.mark.unit
async def test_dispatcher_builds_claim_without_task_snapshot() -> None:
    """Events with ``task=None`` still build a claim; ``project_id`` is None."""
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    event = TaskStateChanged(
        mutation_type="transition",
        request_id="req-1",
        requested_by="engine",
        task_id=sid("task-1"),
        task=None,
        previous_status=TaskStatus.CREATED,
        new_status=TaskStatus.ASSIGNED,
        version=1,
        reason="ready to run",
    )
    await dispatcher.on_task_state_changed(event)

    assert len(queue.published) == 1
    claim = queue.published[0]
    assert claim.task_id == sid("task-1")
    assert claim.project_id is None
    assert claim.previous_status == "created"
    assert claim.new_status == "assigned"


@pytest.mark.unit
def test_queue_config_requires_distributed_bus_via_root_config() -> None:
    """queue.enabled + internal bus should fail RootConfig validation."""
    from synthorg.config.schema import RootConfig
    from synthorg.organization.enums import CompanyType

    with pytest.raises(
        ValueError,
        match=r"requires communication\.message_bus\.backend",
    ):
        RootConfig(
            company_name="Test Co",
            company_type=CompanyType.CUSTOM,
            queue={"enabled": True},  # type: ignore[arg-type]
        )


def _assert_signature_matches(dispatcher: DistributedDispatcher) -> None:
    """Compile-time check that the dispatcher handler matches the callback type."""
    from collections.abc import Awaitable, Callable

    callback: Callable[[TaskStateChanged], Awaitable[None]] = (
        dispatcher.on_task_state_changed
    )
    assert callable(callback)


@pytest.mark.unit
def test_dispatcher_handler_matches_engine_observer_type() -> None:
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]
    _assert_signature_matches(dispatcher)


@pytest.mark.unit
def test_default_provider_uses_registered_workers_bridge_defaults() -> None:
    """Without a bound provider the retry budget equals the registered defaults.

    The dispatcher is built before ``AppState``; the fail-safe default
    must keep the publish retry budget identical to the registered
    ``workers.dispatcher_publish_*`` defaults (3 / 0.1s / 1.0s) so a
    settings-backend hiccup never silently disables publish retries.
    """
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    retry = dispatcher._build_retry()

    cfg = WorkersBridgeConfig()
    assert retry.max_attempts == cfg.dispatcher_publish_max_attempts == 3


@pytest.mark.unit
def test_set_workers_bridge_provider_takes_effect_on_next_publish() -> None:
    """A late-bound provider's snapshot is read per publish (hot-reload).

    Mutating the value the provider returns between two
    ``_build_retry`` calls is reflected on the next call, proving the
    snapshot is read per publish rather than cached at construction.
    """
    queue = _FakeTaskQueue()
    dispatcher = DistributedDispatcher(task_queue=queue)  # type: ignore[arg-type]

    snapshot = WorkersBridgeConfig(dispatcher_publish_max_attempts=7)
    holder = {"cfg": snapshot}
    dispatcher.set_workers_bridge_provider(lambda: holder["cfg"])

    assert dispatcher._build_retry().max_attempts == 7

    holder["cfg"] = WorkersBridgeConfig(dispatcher_publish_max_attempts=2)
    assert dispatcher._build_retry().max_attempts == 2


@pytest.mark.unit
def test_build_retry_propagates_clock_seam() -> None:
    """The construction-time clock is forwarded into every rebuilt handler."""
    queue = _FakeTaskQueue()
    clock = FakeClock()
    dispatcher = DistributedDispatcher(
        task_queue=queue,  # type: ignore[arg-type]
        clock=clock,
    )

    assert dispatcher._build_retry()._clock is clock

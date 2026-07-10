"""Unit tests for :class:`TaskActivityObserver`.

Exercises the boot-registered TaskEngine observer that publishes task
transitions to the ``tasks`` WS channel and records terminal-run outcomes as
task metrics, using injected fakes so no live app is needed.
"""

import json
from collections.abc import Sequence
from typing import cast

import pytest

from synthorg.api.task_activity_observer import TaskActivityObserver
from synthorg.core.artifact import Artifact, ArtifactType
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.hr.performance.models import TaskMetricRecord
from tests._shared import as_uuid, sid


class _FakeChannels:
    def __init__(self) -> None:
        self.published: list[tuple[str, list[str]]] = []
        self.error: Exception | None = None

    def publish(self, data: str, channels: list[str]) -> None:
        if self.error is not None:
            raise self.error
        self.published.append((data, channels))


class _Recorder:
    def __init__(self) -> None:
        self.records: list[TaskMetricRecord] = []

    async def __call__(self, record: TaskMetricRecord) -> TaskMetricRecord:
        self.records.append(record)
        return record


def _artifact() -> Artifact:
    return Artifact(
        id="artifact-1",
        type=ArtifactType.CODE,
        path="src/main.py",
        task_id=sid("task-1"),
        created_by="agent-1",
    )


def _lister(count: int) -> object:
    async def _list(_task_id: str) -> Sequence[Artifact]:
        return tuple(_artifact() for _ in range(count))

    return _list


def _make_task(status: TaskStatus, *, assigned_to: str | None = "agent-1") -> Task:
    return Task(
        id=as_uuid("task-1"),
        title="Ship auth",
        description="Task activity observer fixture.",
        type=TaskType.DEVELOPMENT,
        project="project-1",
        priority=Priority.MEDIUM,
        status=status,
        assigned_to=assigned_to,
        created_by="engine",
    )


def _event(
    *,
    new_status: TaskStatus,
    previous_status: TaskStatus | None,
    assigned_to: str | None = "agent-1",
) -> TaskStateChanged:
    return TaskStateChanged(
        mutation_type="transition",
        request_id="req-1",
        requested_by="engine",
        task_id=sid("task-1"),
        task=_make_task(new_status, assigned_to=assigned_to),
        previous_status=previous_status,
        new_status=new_status,
        version=1,
        reason="transition",
    )


def _observer(
    channels: _FakeChannels, recorder: _Recorder, *, artifact_count: int = 0
) -> TaskActivityObserver:
    return TaskActivityObserver(
        publish=channels.publish,
        list_artifacts=_lister(artifact_count),  # type: ignore[arg-type]
        record_metric=recorder,
    )


def _payload(channels: _FakeChannels) -> dict[str, object]:
    data, _channels = channels.published[-1]
    return cast("dict[str, object]", json.loads(data)["payload"])


@pytest.mark.unit
class TestTaskActivityObserver:
    async def test_non_terminal_transition_publishes_without_recording(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(
                new_status=TaskStatus.IN_PROGRESS,
                previous_status=TaskStatus.ASSIGNED,
            )
        )
        assert len(channels.published) == 1
        payload = _payload(channels)
        assert payload["to_status"] == "in_progress"
        assert payload["from_status"] == "assigned"
        assert payload["run_outcome"] is None
        assert recorder.records == []

    async def test_failed_transition_publishes_and_records_failure(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(new_status=TaskStatus.FAILED, previous_status=TaskStatus.IN_PROGRESS)
        )
        assert _payload(channels)["run_outcome"] == "failed"
        assert len(recorder.records) == 1
        assert recorder.records[0].is_success is False
        assert recorder.records[0].agent_id == "agent-1"

    async def test_review_with_artifacts_records_success(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=2)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert _payload(channels)["run_outcome"] == "succeeded"
        assert recorder.records[0].is_success is True

    async def test_empty_review_records_non_success(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=0)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert _payload(channels)["run_outcome"] == "empty"
        assert recorder.records[0].is_success is False

    async def test_reentry_into_terminal_does_not_double_record(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=1)(
            _event(
                new_status=TaskStatus.COMPLETED, previous_status=TaskStatus.IN_REVIEW
            )
        )
        # Still publishes the transition, but records nothing (previous status
        # was already terminal -- this is not a fresh run outcome).
        assert len(channels.published) == 1
        assert recorder.records == []

    async def test_publish_fault_does_not_block_metric_record(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        channels.error = RuntimeError("ws down")
        await _observer(channels, recorder)(
            _event(new_status=TaskStatus.FAILED, previous_status=TaskStatus.IN_PROGRESS)
        )
        assert channels.published == []  # publish raised, swallowed
        assert len(recorder.records) == 1  # metric still recorded

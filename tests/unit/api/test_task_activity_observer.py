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
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.hr.performance.models import TaskMetricRecord
from tests._shared import as_uuid, sid


class _FakeChannels:
    def __init__(self) -> None:
        self.published: list[tuple[str, list[str]]] = []
        self.error: Exception | None = None

    async def publish(self, data: str, channels: list[str]) -> None:
        # Async to mirror ``ChannelsPlugin.wait_published`` (the direct-delivery
        # surface the observer is wired to), not the sync ``publish``.
        if self.error is not None:
            raise self.error
        self.published.append((data, channels))


class _Recorder:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.records: list[TaskMetricRecord] = []
        self.error = error

    async def __call__(self, record: TaskMetricRecord) -> TaskMetricRecord:
        if self.error is not None:
            raise self.error
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
    channels: _FakeChannels,
    recorder: _Recorder,
    *,
    artifact_count: int = 0,
    list_error: Exception | None = None,
    resolve_agent: object = None,
    resolve_quality: object = None,
) -> TaskActivityObserver:
    lister: object
    if list_error is not None:

        async def _raise(_task_id: str) -> Sequence[Artifact]:
            raise list_error

        lister = _raise
    else:
        lister = _lister(artifact_count)
    return TaskActivityObserver(
        publish=channels.publish,
        list_artifacts=lister,  # type: ignore[arg-type]
        record_metric=recorder,
        resolve_agent=resolve_agent,  # type: ignore[arg-type]
        resolve_quality=resolve_quality,  # type: ignore[arg-type]
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

    async def test_publish_enriches_assignee_identity(self) -> None:
        from synthorg.api.task_activity_observer import ActivityAgentRef
        from synthorg.core.types import NotBlankStr

        channels, recorder = _FakeChannels(), _Recorder()

        async def _resolve(agent_id: str) -> ActivityAgentRef | None:
            assert agent_id == "agent-1"
            return ActivityAgentRef(
                name=NotBlankStr("Alex"),
                role=NotBlankStr("Engineer"),
                department=NotBlankStr("Engineering"),
            )

        await _observer(channels, recorder, resolve_agent=_resolve)(
            _event(
                new_status=TaskStatus.IN_PROGRESS,
                previous_status=TaskStatus.ASSIGNED,
            )
        )
        payload = _payload(channels)
        assert payload["agent_name"] == "Alex"
        assert payload["agent_role"] == "Engineer"
        assert payload["department"] == "Engineering"

    async def test_publish_without_resolver_leaves_identity_null(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(
                new_status=TaskStatus.IN_PROGRESS,
                previous_status=TaskStatus.ASSIGNED,
            )
        )
        payload = _payload(channels)
        assert payload["agent_name"] is None
        assert payload["department"] is None

    async def test_publish_survives_resolver_fault(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()

        async def _boom(_agent_id: str) -> object:
            msg = "resolver down"
            raise RuntimeError(msg)

        await _observer(channels, recorder, resolve_agent=_boom)(
            _event(
                new_status=TaskStatus.IN_PROGRESS,
                previous_status=TaskStatus.ASSIGNED,
            )
        )
        # The publish still fires; only the name enrichment is dropped.
        assert len(channels.published) == 1
        assert _payload(channels)["agent_name"] is None

    async def test_failed_transition_publishes_and_records_failure(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(new_status=TaskStatus.FAILED, previous_status=TaskStatus.IN_PROGRESS)
        )
        assert _payload(channels)["run_outcome"] == "failed"
        assert len(recorder.records) == 1
        assert recorder.records[0].is_success is False
        assert recorder.records[0].run_outcome == RunOutcome.FAILED
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
        assert recorder.records[0].run_outcome == RunOutcome.SUCCEEDED

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
        # The persisted record keeps EMPTY distinct from a hard FAILED so the
        # REST activity feed can tell an empty run from a failure.
        assert recorder.records[0].run_outcome == RunOutcome.EMPTY

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

    async def test_record_carries_unmeasured_telemetry(self) -> None:
        # A transition-sourced record has the reliability outcome but no
        # measured cost / latency / tokens: those stay None, never a
        # fabricated zero, so a consumer reads them as unmeasured.
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=2)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        record = recorder.records[0]
        assert record.duration_seconds is None
        assert record.cost is None
        assert record.turns_used is None
        assert record.tokens_used is None

    async def test_artifact_fault_publishes_but_records_nothing(self) -> None:
        # An unknown artifact count leaves the outcome unresolved; the run is
        # recorded as neither success nor failure (recording nothing) so a
        # transient read fault cannot inflate the health success rate.
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, list_error=RuntimeError("db blip"))(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert len(channels.published) == 1
        assert _payload(channels)["run_outcome"] is None
        assert recorder.records == []

    @pytest.mark.parametrize("terminal", [TaskStatus.CANCELLED, TaskStatus.REJECTED])
    async def test_cancelled_or_rejected_is_not_recorded(
        self, terminal: TaskStatus
    ) -> None:
        # CANCELLED / REJECTED are FSM-terminal but never ran, so they carry no
        # run outcome and must never be recorded as a metric.
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(new_status=terminal, previous_status=TaskStatus.IN_PROGRESS)
        )
        assert len(channels.published) == 1
        assert recorder.records == []

    async def test_terminal_without_assignee_publishes_but_records_nothing(
        self,
    ) -> None:
        # A FAILED task may carry no assignee (unlike IN_REVIEW / COMPLETED),
        # so there is nothing to attribute the outcome to: the WS event still
        # fires but no metric is recorded.
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder)(
            _event(
                new_status=TaskStatus.FAILED,
                previous_status=TaskStatus.IN_PROGRESS,
                assigned_to=None,
            )
        )
        assert len(channels.published) == 1  # WS event still fires
        assert recorder.records == []  # nothing to attribute the outcome to

    async def test_metric_record_fault_is_swallowed_and_publish_happened(
        self,
    ) -> None:
        channels = _FakeChannels()
        recorder = _Recorder(error=RuntimeError("tracker down"))
        # A record fault must not propagate out of __call__, and the WS publish
        # (the other independent side effect) must already have happened.
        await _observer(channels, recorder)(
            _event(new_status=TaskStatus.FAILED, previous_status=TaskStatus.IN_PROGRESS)
        )
        assert len(channels.published) == 1
        assert recorder.records == []


@pytest.mark.unit
class TestOracleFedQualityScore:
    """``quality_score`` is the completion oracle's verdict, never derived."""

    async def test_the_resolved_score_is_stamped_onto_the_record(self) -> None:
        seen: list[str] = []

        async def _resolve(task: Task) -> float | None:
            seen.append(str(task.id))
            return 7.5

        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=1, resolve_quality=_resolve)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert recorder.records[0].quality_score == pytest.approx(7.5)
        assert seen == [str(as_uuid("task-1"))]

    async def test_an_unreviewed_task_records_no_score(self) -> None:
        async def _resolve(_task: Task) -> float | None:
            return None

        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=1, resolve_quality=_resolve)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert recorder.records[0].quality_score is None

    async def test_no_resolver_records_no_score(self) -> None:
        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=1)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert recorder.records[0].quality_score is None

    async def test_a_resolver_fault_leaves_the_score_unmeasured(self) -> None:
        # The authoritative gate already ran and acted on its verdict, so a
        # read fault here must cost the score and nothing else: the outcome
        # record still lands rather than being suppressed.
        async def _raise(_task: Task) -> float | None:
            msg = "archive unavailable"
            raise RuntimeError(msg)

        channels, recorder = _FakeChannels(), _Recorder()
        await _observer(channels, recorder, artifact_count=1, resolve_quality=_raise)(
            _event(
                new_status=TaskStatus.IN_REVIEW,
                previous_status=TaskStatus.IN_PROGRESS,
            )
        )
        assert len(recorder.records) == 1
        assert recorder.records[0].quality_score is None
        assert recorder.records[0].is_success is True

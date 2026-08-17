"""Unit tests for task_engine_apply dispatch and apply functions."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from typeguard import suppress_type_checks

from synthorg.core.task_enums import TaskStatus
from synthorg.engine.task_engine_apply import (
    apply_cancel,
    apply_create,
    apply_delete,
    apply_transition,
    apply_update,
    dispatch,
)
from synthorg.engine.task_engine_models import (
    CancelTaskMutation,
    CreateTaskMutation,
    DeleteTaskMutation,
    TaskMutationResult,
    TransitionTaskMutation,
    UpdateTaskMutation,
)
from synthorg.engine.task_engine_version import TaskSpanTracker, VersionTracker
from tests._shared import FakeClock
from tests.unit.engine.task_engine_helpers import FakePersistence, make_create_data


@pytest.fixture
def persistence() -> FakePersistence:
    return FakePersistence()


@pytest.fixture
def versions() -> VersionTracker:
    return VersionTracker()


# ── Dispatch routing ─────────────────────────────────────────


@pytest.mark.unit
class TestDispatch:
    """Tests for mutation dispatch routing."""

    async def test_dispatch_create(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        result = await dispatch(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None

    async def test_dispatch_unknown_type_raises(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Unknown mutation type raises TypeError."""

        class FakeMutation:
            mutation_type = "fake"
            request_id = "req-1"
            requested_by = "alice"

        with (
            suppress_type_checks(),
            pytest.raises(TypeError, match="Unknown mutation type"),
        ):
            await dispatch(FakeMutation(), persistence, versions)  # type: ignore[arg-type]


# ── apply_create ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyCreate:
    """Tests for task creation apply logic."""

    async def test_creates_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(title="New Task"),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == "New Task"
        assert isinstance(result.task.id, UUID)
        assert result.version == 1

    async def test_create_validation_error(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Invalid task data returns failure with validation error code.

        assigned_to is valid for CreateTaskData but Task rejects it
        when status is CREATED (assignment consistency invariant).
        """
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(assigned_to="bob"),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"
        assert "Invalid task data" in (result.error or "")

    async def test_create_persists_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        result = await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.task is not None
        stored = await persistence.tasks.get(str(result.task.id))
        assert stored is not None


# ── apply_update ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyUpdate:
    """Tests for task update apply logic."""

    async def _create_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> TaskMutationResult:
        mutation = CreateTaskMutation(
            request_id="req-c",
            requested_by="alice",
            task_data=make_create_data(),
        )
        return await apply_create(
            mutation,
            persistence,  # type: ignore[arg-type]
            versions,
        )

    async def test_update_fields(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            updates={"title": "Updated"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == "Updated"
        assert result.version == 2

    async def test_update_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            updates={"title": "X"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_update_version_conflict(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            updates={"title": "X"},
            expected_version=99,
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "version_conflict"

    async def test_update_empty_no_op(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            updates={},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.title == created.task.title

    async def test_update_validation_error(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """Invalid update data returns failure with validation error code."""
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            updates={"priority": "bogus_priority"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"

    async def test_update_records_previous_status(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = UpdateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            updates={"title": "New"},
        )
        result = await apply_update(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.previous_status == TaskStatus.CREATED


# ── apply_transition ─────────────────────────────────────────


@pytest.mark.unit
class TestApplyTransition:
    """Tests for task status transition apply logic."""

    async def _create_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> TaskMutationResult:
        mutation = CreateTaskMutation(
            request_id="req-c",
            requested_by="alice",
            task_data=make_create_data(),
        )
        return await apply_create(mutation, persistence, versions)  # type: ignore[arg-type]

    async def test_valid_transition(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.status == TaskStatus.ASSIGNED
        assert result.previous_status == TaskStatus.CREATED

    async def test_transition_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_transition_version_conflict(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            target_status=TaskStatus.ASSIGNED,
            reason="Assigning",
            overrides={"assigned_to": "bob"},
            expected_version=99,
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "version_conflict"

    async def test_invalid_transition(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """CREATED -> COMPLETED is not valid."""
        created = await self._create_task(persistence, versions)
        assert created.task is not None
        mutation = TransitionTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(created.task.id),
            target_status=TaskStatus.COMPLETED,
            reason="skip",
            overrides={"assigned_to": "bob"},
        )
        result = await apply_transition(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"


# ── apply_delete ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyDelete:
    """Tests for task deletion apply logic."""

    async def test_delete_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        mutation = DeleteTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(create_result.task.id),
        )
        result = await apply_delete(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.version == 0

        stored = await persistence.tasks.get(str(create_result.task.id))
        assert stored is None

    async def test_delete_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = DeleteTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
        )
        result = await apply_delete(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_delete_removes_version_tracking(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        task_id = str(create_result.task.id)
        assert versions.get(task_id) == 1

        await apply_delete(
            DeleteTaskMutation(
                request_id="req-d",
                requested_by="alice",
                task_id=task_id,
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert versions.get(task_id) == 0


# ── apply_cancel ─────────────────────────────────────────────


@pytest.mark.unit
class TestApplyCancel:
    """Tests for task cancellation apply logic."""

    async def _create_and_assign(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
        clock: FakeClock | None = None,
    ) -> str:
        """Create a task and transition to ASSIGNED, return task_id.

        Returns:
            The created task's id. *clock* stamps the row's ``created_at``,
            which is what every duration observation is measured against.
        """
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
            clock=clock,
        )
        assert create_result.task is not None
        task_id = str(create_result.task.id)
        await apply_transition(
            TransitionTaskMutation(
                request_id="req-t",
                requested_by="alice",
                task_id=task_id,
                target_status=TaskStatus.ASSIGNED,
                reason="Assign",
                overrides={"assigned_to": "bob"},
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        return task_id

    async def test_cancel_assigned_task(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        task_id = await self._create_and_assign(persistence, versions)
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=task_id,
            reason="No longer needed",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is True
        assert result.task is not None
        assert result.task.status == TaskStatus.CANCELLED
        assert result.previous_status == TaskStatus.ASSIGNED

    async def test_cancel_not_found(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id="task-nonexistent",
            reason="test",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "not_found"

    async def test_cancel_invalid_status(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """CREATED -> CANCELLED is not a valid transition."""
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        mutation = CancelTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_id=str(create_result.task.id),
            reason="Oops",
        )
        result = await apply_cancel(mutation, persistence, versions)  # type: ignore[arg-type]
        assert result.success is False
        assert result.error_code == "validation"


# ── record_task_run wiring ───────────────────────────────────


@pytest.mark.unit
class TestRecordTaskRunWiring:
    """Verify the task-engine apply path emits metrics on terminal hops."""

    @staticmethod
    async def _create_and_assign(
        persistence: FakePersistence,
        versions: VersionTracker,
        clock: FakeClock | None = None,
    ) -> str:
        """Create a task and drive it to ASSIGNED.

        Returns:
            The created task's id. *clock* stamps the row's ``created_at``,
            which is the baseline every duration observation measures from.
        """
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
            clock=clock,
        )
        assert create_result.task is not None
        task_id = str(create_result.task.id)
        await apply_transition(
            TransitionTaskMutation(
                request_id="req-a",
                requested_by="alice",
                task_id=task_id,
                target_status=TaskStatus.ASSIGNED,
                reason="Assign",
                overrides={"assigned_to": "bob"},
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        return task_id

    async def test_terminal_transition_records_task_run(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """FAILED transition from IN_PROGRESS emits ``record_task_run``.

        Creates the task 12.5 s before a frozen ``now`` so the test asserts a
        concrete ``duration_sec`` value rather than the trivial ``>= 0.0``
        bound -- a regression that always emits ``0.0`` would pass the looser
        assertion silently. The baseline is the row's own ``created_at``, so
        the creating clock is what pins it.
        """
        frozen_now = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)
        task_id = await self._create_and_assign(
            persistence,
            versions,
            clock=FakeClock(start=frozen_now - timedelta(seconds=12.5)),
        )
        # ASSIGNED -> IN_PROGRESS, then IN_PROGRESS -> FAILED
        # (IN_PROGRESS -> COMPLETED is not a valid transition;
        # COMPLETED requires IN_REVIEW first).
        await apply_transition(
            TransitionTaskMutation(
                request_id="req-p",
                requested_by="alice",
                task_id=task_id,
                target_status=TaskStatus.IN_PROGRESS,
                reason="start",
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        with patch(
            "synthorg.engine.task_engine_apply.record_task_run",
        ) as mock_record:
            await apply_transition(
                TransitionTaskMutation(
                    request_id="req-f",
                    requested_by="alice",
                    task_id=task_id,
                    target_status=TaskStatus.FAILED,
                    reason="boom",
                ),
                persistence,  # type: ignore[arg-type]
                versions,
                clock=FakeClock(start=frozen_now),
            )
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["outcome"] == "failed"
        assert kwargs["duration_sec"] == pytest.approx(12.5)

    async def test_rejected_transition_records_task_run(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """REJECTED is in the recorded-outcome table, and fires from CREATED."""
        frozen_now = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
            clock=FakeClock(start=frozen_now - timedelta(seconds=4.25)),
        )
        assert create_result.task is not None

        with patch(
            "synthorg.engine.task_engine_apply.record_task_run",
        ) as mock_record:
            await apply_transition(
                TransitionTaskMutation(
                    request_id="req-r",
                    requested_by="alice",
                    task_id=str(create_result.task.id),
                    target_status=TaskStatus.REJECTED,
                    reason="out of scope",
                ),
                persistence,  # type: ignore[arg-type]
                versions,
                clock=FakeClock(start=frozen_now),
            )

        kwargs = mock_record.call_args.kwargs
        assert kwargs["outcome"] == "rejected"
        assert kwargs["duration_sec"] == pytest.approx(4.25)

    async def test_a_retry_still_measures_from_the_original_creation(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """The property the deleted in-process tracker existed to protect.

        A task that failed and was reassigned must not have its clock reset
        at the retry: the second terminal hop still measures from when the
        task was filed. It holds because ``created_at`` is on the immutable
        row and every transition is a ``model_copy`` that leaves it alone,
        which is exactly the kind of structural truth that stops being true
        without anything failing.
        """
        filed_at = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)
        task_id = await self._create_and_assign(
            persistence,
            versions,
            clock=FakeClock(start=filed_at),
        )
        # The whole chain runs inside the patch because ``f1`` is itself a
        # terminal hop: left outside, it reaches the real process-wide metrics
        # recorder as a side effect of a test about arithmetic.
        with patch(
            "synthorg.engine.task_engine_apply.record_task_run",
        ) as mock_record:
            for step, target in (
                ("p1", TaskStatus.IN_PROGRESS),
                ("f1", TaskStatus.FAILED),
                ("a2", TaskStatus.ASSIGNED),
                ("p2", TaskStatus.IN_PROGRESS),
            ):
                await apply_transition(
                    TransitionTaskMutation(
                        request_id=f"req-{step}",
                        requested_by="alice",
                        task_id=task_id,
                        target_status=target,
                        reason=step,
                        overrides={"assigned_to": "bob"}
                        if target is TaskStatus.ASSIGNED
                        else {},
                    ),
                    persistence,  # type: ignore[arg-type]
                    versions,
                )
            mock_record.reset_mock()
            await apply_transition(
                TransitionTaskMutation(
                    request_id="req-f2",
                    requested_by="alice",
                    task_id=task_id,
                    target_status=TaskStatus.FAILED,
                    reason="failed again",
                ),
                persistence,  # type: ignore[arg-type]
                versions,
                clock=FakeClock(start=filed_at + timedelta(seconds=90)),
            )

        # 90s from FILING, not from the retry that started seconds ago.
        assert mock_record.call_args.kwargs["duration_sec"] == pytest.approx(90.0)

    async def test_non_terminal_transition_does_not_record_task_run(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """CREATED -> ASSIGNED is not a terminal hop; no metric fires."""
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
        )
        assert create_result.task is not None
        with patch(
            "synthorg.engine.task_engine_apply.record_task_run",
        ) as mock_record:
            await apply_transition(
                TransitionTaskMutation(
                    request_id="req-a",
                    requested_by="alice",
                    task_id=str(create_result.task.id),
                    target_status=TaskStatus.ASSIGNED,
                    reason="assign",
                    overrides={"assigned_to": "bob"},
                ),
                persistence,  # type: ignore[arg-type]
                versions,
            )
        mock_record.assert_not_called()

    async def test_apply_cancel_records_task_run(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        """``apply_cancel`` emits ``record_task_run`` with outcome 'cancelled'.

        Same frozen-clock pattern as
        :meth:`test_terminal_transition_records_task_run`: creates the task
        7.25 s before a deterministic ``now`` so the recorded
        ``duration_sec`` is asserted to an exact value.
        """
        frozen_now = datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC)
        task_id = await self._create_and_assign(
            persistence,
            versions,
            clock=FakeClock(start=frozen_now - timedelta(seconds=7.25)),
        )
        with patch(
            "synthorg.engine.task_engine_apply.record_task_run",
        ) as mock_record:
            await apply_cancel(
                CancelTaskMutation(
                    request_id="req-x",
                    requested_by="alice",
                    task_id=task_id,
                    reason="No longer needed",
                ),
                persistence,  # type: ignore[arg-type]
                versions,
                clock=FakeClock(start=frozen_now),
            )
        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["outcome"] == "cancelled"
        assert kwargs["duration_sec"] == pytest.approx(7.25)


@pytest.mark.unit
class TestARetryGetsItsOwnSpan:
    """Closing the failed run's span is only half of what the close promises.

    A failed task is reassigned rather than abandoned, so without a fresh
    span the retry and everything after it are untraced: the task shows one
    span covering the attempt that failed and nothing for the work that
    actually delivered.
    """

    @staticmethod
    def _tracker_with_exporter() -> tuple[TaskSpanTracker, InMemorySpanExporter]:
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return TaskSpanTracker(tracer=provider.get_tracer("test")), exporter

    async def _hop(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
        spans: TaskSpanTracker,
        *,
        task_id: str,
        target: TaskStatus,
        step: str,
    ) -> None:
        await apply_transition(
            TransitionTaskMutation(
                request_id=f"req-{step}",
                requested_by="alice",
                task_id=task_id,
                target_status=target,
                reason=step,
                overrides={"assigned_to": "bob"}
                if target is TaskStatus.ASSIGNED
                else {},
            ),
            persistence,  # type: ignore[arg-type]
            versions,
            spans=spans,
        )

    async def test_reassignment_out_of_failed_opens_a_second_span(
        self,
        persistence: FakePersistence,
        versions: VersionTracker,
    ) -> None:
        spans, exporter = self._tracker_with_exporter()
        create_result = await apply_create(
            CreateTaskMutation(
                request_id="req-c",
                requested_by="alice",
                task_data=make_create_data(),
            ),
            persistence,  # type: ignore[arg-type]
            versions,
            spans=spans,
        )
        assert create_result.task is not None
        task_id = str(create_result.task.id)

        for target, step in (
            (TaskStatus.ASSIGNED, "a1"),
            (TaskStatus.IN_PROGRESS, "p1"),
            (TaskStatus.FAILED, "f1"),
        ):
            await self._hop(
                persistence, versions, spans, task_id=task_id, target=target, step=step
            )
        assert len(exporter.get_finished_spans()) == 1

        await self._hop(
            persistence,
            versions,
            spans,
            task_id=task_id,
            target=TaskStatus.ASSIGNED,
            step="a2",
        )
        # Still one FINISHED span; the retry's is open, which is the whole
        # point. Ending it is what proves a second one was ever started.
        assert len(exporter.get_finished_spans()) == 1

        for target, step in (
            (TaskStatus.IN_PROGRESS, "p2"),
            (TaskStatus.IN_REVIEW, "r2"),
            (TaskStatus.COMPLETED, "c2"),
        ):
            await self._hop(
                persistence, versions, spans, task_id=task_id, target=target, step=step
            )

        finished = exporter.get_finished_spans()
        assert len(finished) == 2
        assert [dict(s.attributes or {})["task.status.final"] for s in finished] == [
            "failed",
            "completed",
        ]

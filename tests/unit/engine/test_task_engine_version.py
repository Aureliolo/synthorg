"""Unit tests for VersionTracker."""

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from synthorg.engine.errors import TaskVersionConflictError
from synthorg.engine.task_engine_version import TaskSpanTracker, VersionTracker


@pytest.mark.unit
class TestVersionTracker:
    """Tests for in-memory per-task version counter."""

    def test_seed_sets_version_to_one(self) -> None:
        vt = VersionTracker()
        vt.seed("task-1")
        assert vt.get("task-1") == 1

    def test_seed_is_idempotent(self) -> None:
        vt = VersionTracker()
        vt.seed("task-1")
        vt.seed("task-1")
        assert vt.get("task-1") == 1

    def test_seed_does_not_reset_after_bump(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 1)
        vt.bump("task-1")
        vt.seed("task-1")
        assert vt.get("task-1") == 2

    def test_set_initial(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 5)
        assert vt.get("task-1") == 5

    def test_set_initial_overwrites(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 5)
        vt.set_initial("task-1", 10)
        assert vt.get("task-1") == 10

    def test_bump_increments(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 1)
        assert vt.bump("task-1") == 2
        assert vt.bump("task-1") == 3

    def test_bump_auto_seeds(self) -> None:
        """Bumping an unknown task seeds at 1, then increments to 2."""
        vt = VersionTracker()
        assert vt.bump("task-1") == 2

    def test_get_returns_zero_for_untracked(self) -> None:
        vt = VersionTracker()
        assert vt.get("task-unknown") == 0

    def test_remove_clears_tracking(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        vt.remove("task-1")
        assert vt.get("task-1") == 0

    def test_remove_nonexistent_is_noop(self) -> None:
        vt = VersionTracker()
        vt.remove("task-unknown")  # no error

    def test_check_passes_when_none(self) -> None:
        vt = VersionTracker()
        vt.check("task-1", None)  # no error

    def test_check_passes_when_version_matches(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        vt.check("task-1", 3)  # no error

    def test_check_raises_on_conflict(self) -> None:
        vt = VersionTracker()
        vt.set_initial("task-1", 3)
        with pytest.raises(
            TaskVersionConflictError,
            match="expected 99, current 3",
        ):
            vt.check("task-1", 99)

    def test_check_seeds_unknown_task(self) -> None:
        """First check on unknown task seeds at 1 then validates."""
        vt = VersionTracker()
        vt.check("task-1", 1)  # seeds at 1, matches
        assert vt.get("task-1") == 1

    def test_check_seeds_then_rejects_mismatch(self) -> None:
        vt = VersionTracker()
        with pytest.raises(TaskVersionConflictError, match="expected 5"):
            vt.check("task-1", 5)

    def test_set_initial_rejects_zero(self) -> None:
        """set_initial must reject version=0."""
        vt = VersionTracker()
        with pytest.raises(ValueError, match="must be >= 1"):
            vt.set_initial("task-1", 0)

    def test_set_initial_rejects_negative(self) -> None:
        """set_initial must reject negative versions."""
        vt = VersionTracker()
        with pytest.raises(ValueError, match="must be >= 1"):
            vt.set_initial("task-1", -5)


@pytest.mark.unit
class TestTaskSpanTracker:
    """Tests for the per-task ``task.run`` span lifecycle tracker."""

    @staticmethod
    def _tracker_with_exporter() -> tuple[TaskSpanTracker, InMemorySpanExporter]:
        exporter = InMemorySpanExporter()
        provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("test")
        return TaskSpanTracker(tracer=tracer), exporter

    def test_start_then_end_emits_task_run_span(self) -> None:
        tracker, exporter = self._tracker_with_exporter()
        tracker.start("task-1", task_type="development")
        # No span finished until end().
        assert exporter.get_finished_spans() == ()
        tracker.end("task-1", final_status="completed")
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "task.run"
        attrs = dict(spans[0].attributes or {})
        assert attrs["task.id"] == "task-1"
        assert attrs["task.type"] == "development"
        assert attrs["task.status.final"] == "completed"

    def test_end_unknown_task_is_noop(self) -> None:
        tracker, exporter = self._tracker_with_exporter()
        tracker.end("never-started", final_status="completed")
        assert exporter.get_finished_spans() == ()

    def test_remove_ends_span_without_final_status(self) -> None:
        tracker, exporter = self._tracker_with_exporter()
        tracker.start("task-1", task_type="research")
        tracker.remove("task-1")
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert "task.status.final" not in dict(spans[0].attributes or {})

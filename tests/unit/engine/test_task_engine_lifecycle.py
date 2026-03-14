"""Lifecycle and config tests for TaskEngine."""

import pytest

from synthorg.engine.errors import TaskEngineNotRunningError
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_engine_models import CreateTaskMutation
from tests.unit.engine.task_engine_helpers import FakePersistence, make_create_data

# ── Lifecycle tests ───────────────────────────────────────────


@pytest.mark.unit
class TestTaskEngineLifecycle:
    """Tests for start/stop lifecycle."""

    async def test_start_sets_running(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        initial = eng.is_running
        assert not initial
        eng.start()
        assert eng.is_running
        await eng.stop(timeout=2.0)
        assert not eng.is_running

    async def test_double_start_raises(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        with pytest.raises(RuntimeError, match="already running"):
            eng.start()
        await eng.stop(timeout=2.0)

    async def test_stop_idempotent(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        await eng.stop(timeout=2.0)
        await eng.stop(timeout=2.0)  # no error

    async def test_restart(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        eng.start()
        await eng.stop(timeout=2.0)
        eng.start()
        assert eng.is_running
        await eng.stop(timeout=2.0)


# ── Submit to stopped engine ──────────────────────────────────


@pytest.mark.unit
class TestSubmitToStoppedEngine:
    """Submitting to a stopped engine raises TaskEngineNotRunningError."""

    async def test_submit_raises(
        self,
        persistence: FakePersistence,
    ) -> None:
        eng = TaskEngine(persistence=persistence)  # type: ignore[arg-type]
        mutation = CreateTaskMutation(
            request_id="req-1",
            requested_by="alice",
            task_data=make_create_data(),
        )
        with pytest.raises(TaskEngineNotRunningError):
            await eng.submit(mutation)


# ── TaskEngineConfig ──────────────────────────────────────────


@pytest.mark.unit
class TestTaskEngineConfig:
    """Tests for TaskEngineConfig model."""

    def test_defaults(self) -> None:
        cfg = TaskEngineConfig()
        assert cfg.max_queue_size == 1000
        assert cfg.drain_timeout_seconds == 10.0
        assert cfg.publish_snapshots is True

    def test_custom_values(self) -> None:
        cfg = TaskEngineConfig(
            max_queue_size=500,
            drain_timeout_seconds=5.0,
            publish_snapshots=False,
        )
        assert cfg.max_queue_size == 500
        assert cfg.drain_timeout_seconds == 5.0
        assert cfg.publish_snapshots is False

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        cfg = TaskEngineConfig()
        with pytest.raises(ValidationError):
            cfg.max_queue_size = 999  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_queue_size", -1),
            ("drain_timeout_seconds", 0),
            ("drain_timeout_seconds", -1.0),
            ("drain_timeout_seconds", 301),
        ],
        ids=[
            "negative_queue_size",
            "zero_drain_timeout",
            "negative_drain_timeout",
            "drain_timeout_above_max",
        ],
    )
    def test_rejects_out_of_range(self, field: str, value: object) -> None:
        from typing import Any

        from pydantic import ValidationError

        kwargs: dict[str, Any] = {field: value}
        with pytest.raises(ValidationError):
            TaskEngineConfig(**kwargs)

    def test_zero_queue_size_allowed(self) -> None:
        """Zero means unbounded — should be accepted."""
        cfg = TaskEngineConfig(max_queue_size=0)
        assert cfg.max_queue_size == 0

    def test_drain_timeout_upper_boundary(self) -> None:
        """Exactly 300 should be accepted."""
        cfg = TaskEngineConfig(drain_timeout_seconds=300.0)
        assert cfg.drain_timeout_seconds == 300.0

"""Tests for the graceful shutdown strategy and manager."""

import asyncio
import signal
import sys
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from synthorg.config.schema import GracefulShutdownConfig
from synthorg.engine.shutdown import (
    CheckpointSaver,
    CooperativeTimeoutStrategy,
    ShutdownManager,
    ShutdownResult,
    ShutdownStrategy,
    _log_post_cancel_exceptions,
)
from synthorg.engine.shutdown_strategies import (
    CheckpointAndStopStrategy,
    FinishCurrentToolStrategy,
    ImmediateCancelStrategy,
    build_shutdown_strategy,
)

# ── Protocol compliance ──────────────────────────────────────────


@pytest.mark.unit
class TestShutdownStrategyProtocol:
    """CooperativeTimeoutStrategy satisfies ShutdownStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        assert isinstance(strategy, ShutdownStrategy)

    def test_result_model_is_frozen(self) -> None:
        result = ShutdownResult(
            strategy_type="cooperative_timeout",
            tasks_interrupted=0,
            tasks_completed=0,
            cleanup_completed=True,
            duration_seconds=0.1,
        )
        with pytest.raises(ValidationError, match="frozen"):
            result.tasks_interrupted = 5  # type: ignore[misc]


# ── Request / check ──────────────────────────────────────────────


@pytest.mark.unit
class TestCooperativeTimeoutRequestShutdown:
    """request_shutdown + is_shutting_down event toggling."""

    def test_not_shutting_down_initially(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        assert strategy.is_shutting_down() is False

    def test_request_sets_shutting_down(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        strategy.request_shutdown()
        assert strategy.is_shutting_down() is True

    def test_idempotent_request(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        strategy.request_shutdown()
        strategy.request_shutdown()
        assert strategy.is_shutting_down() is True

    def test_get_strategy_type(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        assert strategy.get_strategy_type() == "cooperative_timeout"


# ── execute_shutdown -- cooperative exit ──────────────────────────


@pytest.mark.unit
class TestCooperativeTimeoutExecuteCooperative:
    """Tasks that check the shutdown event and exit cooperatively."""

    async def test_all_tasks_exit_cooperatively(self) -> None:
        strategy = CooperativeTimeoutStrategy(grace_seconds=5.0)
        shutdown_event = strategy._shutdown_event

        async def cooperative_task() -> None:
            await shutdown_event.wait()

        task = asyncio.create_task(cooperative_task())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed == 1
        assert result.tasks_interrupted == 0
        assert result.cleanup_completed is True
        assert result.strategy_type == "cooperative_timeout"
        assert result.duration_seconds > 0

    async def test_empty_tasks(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[],
        )
        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 0


# ── execute_shutdown -- force cancel ──────────────────────────────


@pytest.mark.unit
class TestCooperativeTimeoutForceCancel:
    """Tasks that ignore the shutdown event are force-cancelled."""

    async def test_stubborn_task_is_force_cancelled(self) -> None:
        strategy = CooperativeTimeoutStrategy(grace_seconds=0.1)

        async def stubborn_task() -> None:
            await asyncio.Event().wait()  # ignores shutdown

        task = asyncio.create_task(stubborn_task())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 1

    async def test_mixed_cooperative_and_stubborn(self) -> None:
        strategy = CooperativeTimeoutStrategy(grace_seconds=0.1)
        shutdown_event = strategy._shutdown_event

        async def cooperative() -> None:
            await shutdown_event.wait()

        async def stubborn() -> None:
            await asyncio.Event().wait()

        t1 = asyncio.create_task(cooperative())
        t2 = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": t1, "t2": t2},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed + result.tasks_interrupted == 2
        assert result.tasks_interrupted >= 1


# ── execute_shutdown -- cleanup callbacks ─────────────────────────


@pytest.mark.unit
class TestCooperativeTimeoutCleanup:
    """Cleanup callbacks run within the time budget."""

    async def test_cleanup_callbacks_run(self) -> None:
        strategy = CooperativeTimeoutStrategy(cleanup_seconds=5.0)
        ran = []

        async def cb1() -> None:
            ran.append("cb1")

        async def cb2() -> None:
            ran.append("cb2")

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[cb1, cb2],
        )

        assert ran == ["cb1", "cb2"]
        assert result.cleanup_completed is True

    async def test_cleanup_timeout(self) -> None:
        strategy = CooperativeTimeoutStrategy(cleanup_seconds=0.1)

        async def slow_callback() -> None:
            await asyncio.Event().wait()

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[slow_callback],
        )

        assert result.cleanup_completed is False

    async def test_empty_cleanup(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[],
        )
        assert result.cleanup_completed is True


# ── ShutdownManager ──────────────────────────────────────────────


@pytest.mark.unit
class TestShutdownManagerTaskTracking:
    """Register / unregister tasks."""

    def test_register_and_unregister(self) -> None:
        manager = ShutdownManager()
        mock_task = MagicMock(spec=asyncio.Task)
        manager.register_task("task-1", mock_task)
        assert "task-1" in manager._running_tasks
        manager.unregister_task("task-1")
        assert "task-1" not in manager._running_tasks

    def test_unregister_missing_is_noop(self) -> None:
        manager = ShutdownManager()
        manager.unregister_task("nonexistent")

    def test_register_cleanup(self) -> None:
        manager = ShutdownManager()

        async def cb() -> None:
            pass

        manager.register_cleanup(cb)
        assert len(manager._cleanup_callbacks) == 1

    def test_is_shutting_down_delegates(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        assert manager.is_shutting_down() is False
        strategy.request_shutdown()
        assert manager.is_shutting_down() is True

    def test_register_task_during_shutdown_raises(self) -> None:
        """Drain gate: registering a task after shutdown raises RuntimeError."""
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        strategy.request_shutdown()
        mock_task = MagicMock(spec=asyncio.Task)
        with pytest.raises(RuntimeError, match="shutdown already in progress"):
            manager.register_task("late-task", mock_task)


@pytest.mark.unit
class TestShutdownManagerSignalHandlers:
    """Signal handler installation."""

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-only test")
    def test_install_signal_handlers_unix(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        mock_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=mock_loop):
            manager.install_signal_handlers()
        assert mock_loop.add_signal_handler.call_count == 2
        assert manager._signals_installed is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_install_signal_handlers_windows(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        with patch("signal.signal") as mock_signal:
            manager.install_signal_handlers()
        assert mock_signal.call_count == 2
        assert manager._signals_installed is True

    def test_install_idempotent(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        if sys.platform == "win32":
            with patch("signal.signal"):
                manager.install_signal_handlers()
                manager.install_signal_handlers()  # second call is noop
        else:
            mock_loop = MagicMock()
            with patch("asyncio.get_running_loop", return_value=mock_loop):
                manager.install_signal_handlers()
                manager.install_signal_handlers()
            assert mock_loop.add_signal_handler.call_count == 2  # not 4


@pytest.mark.unit
class TestShutdownManagerInitiateShutdown:
    """Full initiate_shutdown delegates to strategy."""

    async def test_initiate_shutdown(self) -> None:
        strategy = CooperativeTimeoutStrategy(grace_seconds=0.1)
        manager = ShutdownManager(strategy=strategy)
        result = await manager.initiate_shutdown()
        assert isinstance(result, ShutdownResult)
        assert result.strategy_type == "cooperative_timeout"

    def test_default_strategy(self) -> None:
        manager = ShutdownManager()
        assert isinstance(manager.strategy, CooperativeTimeoutStrategy)


@pytest.mark.unit
class TestShutdownManagerSignalHandling:
    """Signal handler triggers request_shutdown."""

    def test_handle_signal_unix(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        manager._handle_signal(signal.SIGINT)
        assert strategy.is_shutting_down() is True

    def test_handle_signal_threadsafe_with_loop(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        mock_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=mock_loop):
            manager._handle_signal_threadsafe(signal.SIGINT.value, None)
        mock_loop.call_soon_threadsafe.assert_called_once()
        # Execute the callback to verify it actually calls request_shutdown.
        callback = mock_loop.call_soon_threadsafe.call_args[0][0]
        assert callable(callback)
        callback()
        assert strategy.is_shutting_down() is True

    def test_handle_signal_threadsafe_no_loop(self) -> None:
        strategy = CooperativeTimeoutStrategy()
        manager = ShutdownManager(strategy=strategy)
        with patch(
            "asyncio.get_running_loop",
            side_effect=RuntimeError("no loop"),
        ):
            manager._handle_signal_threadsafe(signal.SIGINT.value, None)
        assert strategy.is_shutting_down() is True


# ── Constructor validation ────────────────────────────────────────


@pytest.mark.unit
class TestCooperativeTimeoutValidation:
    """Constructor rejects non-positive timeout values."""

    def test_zero_grace_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="grace_seconds must be positive"):
            CooperativeTimeoutStrategy(grace_seconds=0)

    def test_negative_grace_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="grace_seconds must be positive"):
            CooperativeTimeoutStrategy(grace_seconds=-1.0)

    def test_zero_cleanup_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            CooperativeTimeoutStrategy(cleanup_seconds=0)

    def test_negative_cleanup_seconds_rejected(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            CooperativeTimeoutStrategy(cleanup_seconds=-5.0)


# ── Cleanup callback exception isolation ──────────────────────────


@pytest.mark.unit
class TestCleanupCallbackExceptionIsolation:
    """A failing callback doesn't prevent subsequent callbacks from running."""

    async def test_failing_callback_does_not_block_others(self) -> None:
        strategy = CooperativeTimeoutStrategy(cleanup_seconds=5.0)
        ran = []

        async def cb_ok_1() -> None:
            ran.append("cb1")

        async def cb_fail() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        async def cb_ok_2() -> None:
            ran.append("cb2")

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[cb_ok_1, cb_fail, cb_ok_2],
        )

        assert "cb1" in ran
        assert "cb2" in ran
        # cleanup_completed is False because one callback failed
        assert result.cleanup_completed is False


# ── GracefulShutdownConfig validation ─────────────────────────────


@pytest.mark.unit
class TestGracefulShutdownConfig:
    """Config model boundary validation."""

    def test_defaults(self) -> None:
        config = GracefulShutdownConfig()
        assert config.strategy == "cooperative_timeout"
        assert config.grace_seconds == 30.0
        assert config.cleanup_seconds == 5.0

    def test_grace_seconds_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(grace_seconds=301)

    def test_cleanup_seconds_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(cleanup_seconds=61)

    def test_zero_grace_seconds_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(grace_seconds=0)

    def test_blank_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(strategy="   ")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, 60.0),
            (300, 300.0),
        ],
        ids=["default", "at-max"],
    )
    def test_tool_timeout_valid(
        self,
        value: float | None,
        expected: float,
    ) -> None:
        if value is None:
            config = GracefulShutdownConfig()
        else:
            config = GracefulShutdownConfig(tool_timeout_seconds=value)
        assert config.tool_timeout_seconds == expected

    @pytest.mark.parametrize(
        "value",
        [0, 301],
        ids=["zero", "above-max"],
    )
    def test_tool_timeout_rejected(self, value: float) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(tool_timeout_seconds=value)


# ── _log_post_cancel_exceptions ───────────────────────────────────


@pytest.mark.unit
class TestLogPostCancelExceptions:
    """Extracted helper retrieves exceptions without swallowing them."""

    def test_skips_cancelled_tasks(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = True
        _log_post_cancel_exceptions({task})
        task.exception.assert_not_called()

    def test_logs_task_exception(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        task.get_name.return_value = "test-task"
        _log_post_cancel_exceptions({task})
        task.exception.assert_called_once()

    def test_handles_no_exception(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.return_value = None
        task.get_name.return_value = "test-task"
        _log_post_cancel_exceptions({task})
        task.exception.assert_called_once()

    def test_handles_invalid_state_error(self) -> None:
        task = MagicMock(spec=asyncio.Task)
        task.cancelled.return_value = False
        task.exception.side_effect = asyncio.InvalidStateError
        task.get_name.return_value = "test-task"
        _log_post_cancel_exceptions({task})


# ── Signal handler recovery ──────────────────────────────────────


@pytest.mark.unit
class TestSignalHandlerRecovery:
    """Signal handler falls back to loop.stop() on strategy failure."""

    def test_handle_signal_unix_falls_back_to_loop_stop(self) -> None:
        strategy = MagicMock(spec=ShutdownStrategy)
        strategy.request_shutdown.side_effect = RuntimeError("broken")
        manager = ShutdownManager(strategy=strategy)
        mock_loop = MagicMock()
        with patch("asyncio.get_running_loop", return_value=mock_loop):
            manager._handle_signal(signal.SIGINT)
        mock_loop.stop.assert_called_once()

    def test_handle_signal_threadsafe_no_loop_stderr_fallback(self) -> None:
        strategy = MagicMock(spec=ShutdownStrategy)
        strategy.request_shutdown.side_effect = RuntimeError("broken")
        manager = ShutdownManager(strategy=strategy)
        with (
            patch(
                "asyncio.get_running_loop",
                side_effect=RuntimeError("no loop"),
            ),
            patch("sys.stderr") as mock_stderr,
        ):
            manager._handle_signal_threadsafe(signal.SIGINT.value, None)
        mock_stderr.write.assert_called_once()

    def test_handle_signal_threadsafe_no_loop_stderr_also_fails(self) -> None:
        strategy = MagicMock(spec=ShutdownStrategy)
        strategy.request_shutdown.side_effect = RuntimeError("broken")
        manager = ShutdownManager(strategy=strategy)
        with (
            patch(
                "asyncio.get_running_loop",
                side_effect=RuntimeError("no loop"),
            ),
            patch("sys.stderr") as mock_stderr,
        ):
            mock_stderr.write.side_effect = OSError("stderr closed")
            # Should not raise even when stderr fails
            manager._handle_signal_threadsafe(signal.SIGINT.value, None)


# ── ShutdownResult.tasks_suspended ──────────────────────────────


@pytest.mark.unit
class TestShutdownResultSuspendedField:
    """tasks_suspended field backward compatibility and validation."""

    def test_default_tasks_suspended_is_zero(self) -> None:
        result = ShutdownResult(
            strategy_type="cooperative_timeout",
            tasks_interrupted=0,
            tasks_completed=1,
            cleanup_completed=True,
            duration_seconds=0.5,
        )
        assert result.tasks_suspended == 0

    def test_tasks_suspended_set(self) -> None:
        result = ShutdownResult(
            strategy_type="checkpoint",
            tasks_interrupted=0,
            tasks_completed=0,
            tasks_suspended=3,
            cleanup_completed=True,
            duration_seconds=1.0,
        )
        assert result.tasks_suspended == 3

    def test_tasks_suspended_frozen(self) -> None:
        result = ShutdownResult(
            strategy_type="checkpoint",
            tasks_interrupted=0,
            tasks_completed=0,
            tasks_suspended=1,
            cleanup_completed=True,
            duration_seconds=0.1,
        )
        with pytest.raises(ValidationError, match="frozen"):
            result.tasks_suspended = 5  # type: ignore[misc]


# ── ImmediateCancelStrategy ─────────────────────────────────────


@pytest.mark.unit
class TestImmediateCancelProtocol:
    """ImmediateCancelStrategy satisfies ShutdownStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        strategy = ImmediateCancelStrategy()
        assert isinstance(strategy, ShutdownStrategy)

    def test_not_shutting_down_initially(self) -> None:
        strategy = ImmediateCancelStrategy()
        assert strategy.is_shutting_down() is False

    def test_request_sets_shutting_down(self) -> None:
        strategy = ImmediateCancelStrategy()
        strategy.request_shutdown()
        assert strategy.is_shutting_down() is True

    def test_get_strategy_type(self) -> None:
        strategy = ImmediateCancelStrategy()
        assert strategy.get_strategy_type() == "immediate"


@pytest.mark.unit
class TestImmediateCancelExecute:
    """All tasks force-cancelled immediately, no grace period."""

    async def test_all_tasks_force_cancelled(self) -> None:
        strategy = ImmediateCancelStrategy()

        async def task_a() -> None:
            await asyncio.Event().wait()

        async def task_b() -> None:
            await asyncio.Event().wait()

        t1 = asyncio.create_task(task_a())
        t2 = asyncio.create_task(task_b())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": t1, "t2": t2},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 2
        assert result.strategy_type == "immediate"

    async def test_no_tasks(self) -> None:
        strategy = ImmediateCancelStrategy()
        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[],
        )
        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 0

    async def test_cleanup_runs(self) -> None:
        strategy = ImmediateCancelStrategy(cleanup_seconds=5.0)
        ran = []

        async def cb() -> None:
            ran.append("done")

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[cb],
        )
        assert ran == ["done"]
        assert result.cleanup_completed is True


@pytest.mark.unit
class TestImmediateCancelValidation:
    """Constructor rejects non-positive values."""

    def test_invalid_cleanup_seconds(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            ImmediateCancelStrategy(cleanup_seconds=0)

    def test_negative_cleanup_seconds(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            ImmediateCancelStrategy(cleanup_seconds=-1.0)


# ── FinishCurrentToolStrategy ───────────────────────────────────


@pytest.mark.unit
class TestFinishCurrentToolProtocol:
    """FinishCurrentToolStrategy satisfies ShutdownStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=60.0)
        assert isinstance(strategy, ShutdownStrategy)

    def test_not_shutting_down_initially(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=60.0)
        assert strategy.is_shutting_down() is False

    def test_request_sets_shutting_down(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=60.0)
        strategy.request_shutdown()
        assert strategy.is_shutting_down() is True

    def test_get_strategy_type(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=60.0)
        assert strategy.get_strategy_type() == "finish_tool"


@pytest.mark.unit
class TestFinishCurrentToolExecute:
    """Tasks wait for tool completion with per-tool timeout."""

    async def test_cooperative_tasks_within_timeout(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=5.0)
        shutdown_event = strategy._shutdown_event

        async def tool_task() -> None:
            await shutdown_event.wait()

        task = asyncio.create_task(tool_task())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed == 1
        assert result.tasks_interrupted == 0

    async def test_stragglers_cancelled_after_timeout(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=0.1)

        async def long_tool() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(long_tool())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 1

    async def test_cleanup_runs(self) -> None:
        strategy = FinishCurrentToolStrategy(
            tool_timeout_seconds=60.0,
            cleanup_seconds=5.0,
        )
        ran = []

        async def cb() -> None:
            ran.append("done")

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[cb],
        )
        assert ran == ["done"]
        assert result.cleanup_completed is True

    async def test_empty_tasks(self) -> None:
        strategy = FinishCurrentToolStrategy(tool_timeout_seconds=60.0)
        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[],
        )
        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 0


@pytest.mark.unit
class TestFinishCurrentToolValidation:
    """Constructor rejects non-positive values."""

    def test_invalid_tool_timeout(self) -> None:
        with pytest.raises(ValueError, match="tool_timeout_seconds must be positive"):
            FinishCurrentToolStrategy(tool_timeout_seconds=0)

    def test_invalid_cleanup_seconds(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            FinishCurrentToolStrategy(
                tool_timeout_seconds=60.0,
                cleanup_seconds=-1.0,
            )


# ── CheckpointAndStopStrategy ──────────────────────────────────


@pytest.mark.unit
class TestCheckpointAndStopProtocol:
    """CheckpointAndStopStrategy satisfies ShutdownStrategy protocol."""

    def test_is_runtime_checkable(self) -> None:
        strategy = CheckpointAndStopStrategy()
        assert isinstance(strategy, ShutdownStrategy)

    def test_not_shutting_down_initially(self) -> None:
        strategy = CheckpointAndStopStrategy()
        assert strategy.is_shutting_down() is False

    def test_request_sets_shutting_down(self) -> None:
        strategy = CheckpointAndStopStrategy()
        strategy.request_shutdown()
        assert strategy.is_shutting_down() is True

    def test_get_strategy_type(self) -> None:
        strategy = CheckpointAndStopStrategy()
        assert strategy.get_strategy_type() == "checkpoint"


@pytest.mark.unit
class TestCheckpointAndStopExecute:
    """Checkpoint-based shutdown with tasks_suspended tracking."""

    async def test_cooperative_exit_counted_as_suspended(self) -> None:
        strategy = CheckpointAndStopStrategy(grace_seconds=5.0)
        shutdown_event = strategy._shutdown_event

        async def cooperative_task() -> None:
            await shutdown_event.wait()

        task = asyncio.create_task(cooperative_task())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 1
        assert result.tasks_completed == 0
        assert result.tasks_interrupted == 0

    async def test_straggler_checkpointed_then_cancelled(self) -> None:
        strategy = CheckpointAndStopStrategy(
            grace_seconds=0.1,
            checkpoint_saver=_make_saver(success=True),
        )

        async def stubborn() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 1
        assert result.tasks_interrupted == 0

    async def test_straggler_checkpoint_fails_counted_as_interrupted(self) -> None:
        strategy = CheckpointAndStopStrategy(
            grace_seconds=0.1,
            checkpoint_saver=_make_saver(success=False),
        )

        async def stubborn() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 0
        assert result.tasks_interrupted == 1

    async def test_no_checkpoint_saver_stragglers_interrupted(self) -> None:
        strategy = CheckpointAndStopStrategy(grace_seconds=0.1)

        async def stubborn() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 0
        assert result.tasks_interrupted == 1

    async def test_checkpoint_saver_raises_counted_as_interrupted(self) -> None:
        async def failing_saver(task_id: str) -> bool:
            msg = "Storage unavailable"
            raise OSError(msg)

        strategy = CheckpointAndStopStrategy(
            grace_seconds=0.1,
            checkpoint_saver=failing_saver,
        )

        async def stubborn() -> None:
            await asyncio.Event().wait()

        task = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"t1": task},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 0
        assert result.tasks_interrupted == 1

    async def test_mixed_cooperative_and_straggler(self) -> None:
        saver_calls: list[str] = []

        async def saver(task_id: str) -> bool:
            saver_calls.append(task_id)
            return True

        strategy = CheckpointAndStopStrategy(
            grace_seconds=0.1,
            checkpoint_saver=saver,
        )
        shutdown_event = strategy._shutdown_event

        async def cooperative() -> None:
            await shutdown_event.wait()

        async def stubborn() -> None:
            await asyncio.Event().wait()

        t1 = asyncio.create_task(cooperative())
        t2 = asyncio.create_task(stubborn())
        result = await strategy.execute_shutdown(
            running_tasks={"coop": t1, "stub": t2},
            cleanup_callbacks=[],
        )

        assert result.tasks_suspended == 2
        assert result.tasks_interrupted == 0
        assert "stub" in saver_calls

    async def test_cleanup_runs(self) -> None:
        strategy = CheckpointAndStopStrategy(cleanup_seconds=5.0)
        ran = []

        async def cb() -> None:
            ran.append("done")

        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[cb],
        )
        assert ran == ["done"]
        assert result.cleanup_completed is True

    async def test_empty_tasks(self) -> None:
        strategy = CheckpointAndStopStrategy()
        result = await strategy.execute_shutdown(
            running_tasks={},
            cleanup_callbacks=[],
        )
        assert result.tasks_suspended == 0
        assert result.tasks_interrupted == 0


@pytest.mark.unit
class TestCheckpointAndStopValidation:
    """Constructor rejects non-positive values."""

    def test_invalid_grace_seconds(self) -> None:
        with pytest.raises(ValueError, match="grace_seconds must be positive"):
            CheckpointAndStopStrategy(grace_seconds=0)

    def test_invalid_cleanup_seconds(self) -> None:
        with pytest.raises(ValueError, match="cleanup_seconds must be positive"):
            CheckpointAndStopStrategy(cleanup_seconds=-1.0)


# ── build_shutdown_strategy factory ─────────────────────────────


@pytest.mark.unit
class TestBuildShutdownStrategy:
    """Factory maps config.strategy to the correct class."""

    def test_cooperative_timeout(self) -> None:
        config = GracefulShutdownConfig(strategy="cooperative_timeout")
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, CooperativeTimeoutStrategy)

    def test_immediate(self) -> None:
        config = GracefulShutdownConfig(strategy="immediate")
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, ImmediateCancelStrategy)

    def test_finish_tool(self) -> None:
        config = GracefulShutdownConfig(strategy="finish_tool")
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, FinishCurrentToolStrategy)

    def test_checkpoint(self) -> None:
        config = GracefulShutdownConfig(strategy="checkpoint")
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, CheckpointAndStopStrategy)

    def test_unknown_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GracefulShutdownConfig(strategy="nonexistent")  # type: ignore[arg-type]

    def test_config_params_propagate(self) -> None:
        config = GracefulShutdownConfig(
            strategy="cooperative_timeout",
            grace_seconds=10.0,
            cleanup_seconds=2.0,
        )
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, CooperativeTimeoutStrategy)
        assert strategy._grace_seconds == 10.0
        assert strategy._cleanup_seconds == 2.0

    def test_tool_timeout_propagates(self) -> None:
        config = GracefulShutdownConfig(
            strategy="finish_tool",
            tool_timeout_seconds=120.0,
        )
        strategy = build_shutdown_strategy(config)
        assert isinstance(strategy, FinishCurrentToolStrategy)
        assert strategy._tool_timeout_seconds == 120.0

    def test_checkpoint_saver_injected(self) -> None:
        config = GracefulShutdownConfig(strategy="checkpoint")
        saver = _make_saver(success=True)
        strategy = build_shutdown_strategy(config, checkpoint_saver=saver)
        assert isinstance(strategy, CheckpointAndStopStrategy)
        assert strategy._checkpoint_saver is saver


# ── Test helpers ────────────────────────────────────────────────


def _make_saver(*, success: bool) -> CheckpointSaver:
    """Build a mock checkpoint saver that returns *success*."""

    async def _saver(task_id: str) -> bool:
        return success

    return _saver

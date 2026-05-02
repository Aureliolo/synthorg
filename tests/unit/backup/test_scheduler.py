"""Tests for BackupScheduler -- periodic background backup task."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.backup.models import BackupTrigger
from synthorg.backup.scheduler import BackupScheduler


def _make_mock_service() -> MagicMock:
    """Build a mock BackupService with an async create_backup."""
    service = MagicMock()
    service.create_backup = AsyncMock()
    return service


def _close_coro_side_effect(
    mock_task: MagicMock,
) -> object:
    """Build a create_task side effect that closes the coroutine."""

    def _side_effect(coro: object, **kwargs: object) -> MagicMock:
        if hasattr(coro, "close"):
            coro.close()
        return mock_task

    return _side_effect


@pytest.mark.unit
class TestSchedulerStart:
    """Tests for start() creating a background task."""

    async def test_start_creates_background_task(self) -> None:
        """start() creates an asyncio task and sets is_running."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        assert not scheduler.is_running

        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False

        with patch(
            "synthorg.backup.scheduler.asyncio.create_task",
            side_effect=_close_coro_side_effect(mock_task),
        ) as mock_ct:
            await scheduler.start()

        assert scheduler.is_running
        mock_ct.assert_called_once()  # type: ignore[unreachable]

    async def test_start_is_noop_when_already_running(self) -> None:
        """start() does nothing if the scheduler is already running."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False

        with patch(
            "synthorg.backup.scheduler.asyncio.create_task",
            side_effect=_close_coro_side_effect(mock_task),
        ) as mock_ct:
            await scheduler.start()
            await scheduler.start()  # second call should be no-op

        assert mock_ct.call_count == 1


@pytest.mark.unit
class TestSchedulerStop:
    """Tests for stop() cancelling the task."""

    async def test_stop_cancels_task(self) -> None:
        """stop() cancels the background task and clears state."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        # Create a task that blocks until cancelled
        async def _forever() -> None:
            await asyncio.Event().wait()

        scheduler._task = asyncio.create_task(_forever())
        assert scheduler.is_running

        await scheduler.stop()

        assert not scheduler.is_running
        assert scheduler._task is None  # type: ignore[unreachable]

    async def test_stop_is_noop_when_not_running(self) -> None:
        """stop() does nothing if the scheduler is not running."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        # Should not raise
        await scheduler.stop()
        assert not scheduler.is_running


@pytest.mark.unit
class TestSchedulerIsRunning:
    """Tests for is_running property."""

    async def test_is_running_false_initially(self) -> None:
        """A new scheduler reports is_running=False."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)
        assert not scheduler.is_running

    async def test_is_running_true_after_start(self) -> None:
        """After start(), is_running is True."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False

        with patch(
            "synthorg.backup.scheduler.asyncio.create_task",
            side_effect=_close_coro_side_effect(mock_task),
        ):
            await scheduler.start()

        assert scheduler.is_running

    async def test_is_running_false_when_task_done(self) -> None:
        """is_running is False if the task has completed."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = True
        scheduler._task = mock_task

        assert not scheduler.is_running


@pytest.mark.unit
class TestSchedulerReschedule:
    """Tests for reschedule() updating the interval."""

    @pytest.mark.parametrize(
        ("initial_hours", "new_hours", "expected_seconds"),
        [
            (1, 2, 7200),
            (6, 12, 43200),
            (24, 1, 3600),
        ],
    )
    async def test_reschedule_updates_interval(
        self,
        initial_hours: int,
        new_hours: int,
        expected_seconds: int,
    ) -> None:
        """reschedule() changes the internal interval in seconds."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=initial_hours)

        scheduler.reschedule(new_hours)

        assert scheduler._interval_seconds == expected_seconds

    @pytest.mark.parametrize("hours", [0, -1, -100])
    async def test_reschedule_rejects_invalid_interval(
        self,
        hours: int,
    ) -> None:
        """reschedule() raises ValueError for interval_hours < 1."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        with pytest.raises(ValueError, match="interval_hours must be >= 1"):
            scheduler.reschedule(hours)


@pytest.mark.unit
class TestSchedulerLoop:
    """Tests for the internal _run_loop calling the service."""

    async def test_loop_calls_create_backup_with_scheduled_trigger(
        self,
    ) -> None:
        """The loop calls service.create_backup(SCHEDULED) after waking."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=1)

        call_count = 0

        async def _counting_wait_for(
            coro: object,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            nonlocal call_count
            call_count += 1
            # Close the unawaited coroutine to avoid RuntimeWarning
            if hasattr(coro, "close"):
                coro.close()
            if call_count >= 2:
                raise asyncio.CancelledError
            raise TimeoutError

        with (
            patch(
                "synthorg.backup.scheduler.asyncio.wait_for",
                _counting_wait_for,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await scheduler._run_loop()

        service.create_backup.assert_called_once_with(BackupTrigger.SCHEDULED)

    async def test_loop_continues_after_backup_error(self) -> None:
        """The loop does not crash when create_backup raises."""
        service = _make_mock_service()
        service.create_backup.side_effect = RuntimeError("boom")
        scheduler = BackupScheduler(service, interval_hours=1)

        call_count = 0

        async def _counting_wait_for(
            coro: object,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            nonlocal call_count
            call_count += 1
            if hasattr(coro, "close"):
                coro.close()
            if call_count >= 3:
                raise asyncio.CancelledError
            raise TimeoutError

        with (
            patch(
                "synthorg.backup.scheduler.asyncio.wait_for",
                _counting_wait_for,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await scheduler._run_loop()

        # Should have been called twice (once per completed iteration)
        assert service.create_backup.call_count == 2

    async def test_loop_propagates_memory_error(self) -> None:
        """MemoryError is not swallowed by the loop."""
        service = _make_mock_service()
        service.create_backup.side_effect = MemoryError("out of memory")
        scheduler = BackupScheduler(service, interval_hours=1)

        async def _single_wait_for(
            coro: object,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError

        with (
            patch(
                "synthorg.backup.scheduler.asyncio.wait_for",
                _single_wait_for,
            ),
            pytest.raises(MemoryError, match="out of memory"),
        ):
            await scheduler._run_loop()

    async def test_loop_uses_configured_interval_as_timeout(self) -> None:
        """The loop passes the configured interval to wait_for timeout."""
        service = _make_mock_service()
        scheduler = BackupScheduler(service, interval_hours=3)

        recorded_timeouts: list[float | None] = []

        async def _recording_wait_for(
            coro: object,
            *,
            timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            if hasattr(coro, "close"):
                coro.close()
            recorded_timeouts.append(timeout)
            raise asyncio.CancelledError

        with (
            patch(
                "synthorg.backup.scheduler.asyncio.wait_for",
                _recording_wait_for,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await scheduler._run_loop()

        assert recorded_timeouts == [3 * 3600]

"""Fine-tuning pipeline orchestrator.

Manages background execution of the five-stage pipeline with state
persistence, cancellation, WebSocket progress events, and resume
of failed runs from the last completed stage.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import FineTuneRunActiveError
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneRequest,
    FineTuneRun,
    FineTuneStatus,
)
from synthorg.memory.embedding.fine_tune_pipeline import run_fine_tune_stages
from synthorg.memory.embedding.fine_tune_run_helpers import (
    build_config,
    to_failed,
)
from synthorg.memory.embedding.fine_tune_ws import (
    ChannelsPlugin,
    publish_ws_event,
)
from synthorg.memory.embedding.training_sources import TrainingDataSource
from synthorg.memory.errors import FineTuneCancelledError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_CANCELLED,
    MEMORY_FINE_TUNE_COMPLETED,
    MEMORY_FINE_TUNE_FAILED,
    MEMORY_FINE_TUNE_PROGRESS,
    MEMORY_FINE_TUNE_STAGE_ENTERED,
    MEMORY_FINE_TUNE_STARTED,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
    FineTuneRunRepository,
)

logger = get_logger(__name__)

# Minimum interval between WS progress events.
_PROGRESS_THROTTLE_SEC: Final[float] = 1.0
# How long ``cancel`` waits for the in-flight pipeline task to stop before
# returning anyway (recovery marks any still-active run FAILED on next boot).
_CANCEL_TIMEOUT_SEC: Final[float] = 30.0


class FineTuneOrchestrator:
    """Background pipeline orchestrator.

    Manages the lifecycle of fine-tuning runs: start, resume,
    cancel, and startup recovery.

    Args:
        run_repo: Repository for run state (protocol-typed; backend-agnostic).
        checkpoint_repo: Repository for checkpoints (protocol-typed).
        settings_service: Runtime settings (for deploy stage).
        channels_plugin: WS plugin exposing ``publish(data, channels=...)``.
        llm_provider: Optional LLM provider for data generation.
        training_data_source: Optional real-trajectory data source. Required
            only when a run selects ``data_source=trajectory``; directory mode
            needs no source.
    """

    def __init__(  # noqa: PLR0913 -- pluggable dependencies threaded for testability
        self,
        *,
        run_repo: FineTuneRunRepository,
        checkpoint_repo: FineTuneCheckpointRepository,
        settings_service: object | None = None,
        channels_plugin: ChannelsPlugin | None = None,
        llm_provider: object | None = None,
        training_data_source: TrainingDataSource | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._checkpoint_repo = checkpoint_repo
        self._settings_service = settings_service
        self._channels_plugin = channels_plugin
        self._llm_provider = llm_provider
        self._training_data_source = training_data_source
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._current_task: asyncio.Task[None] | None = None
        self._cancellation: CancellationToken | None = None
        self._current_run: FineTuneRun | None = None
        # Eager init: start() and cancel() may interleave, so the
        # lock must exist before the first call to either method.
        self._op_lock = asyncio.Lock()  # lint-allow: loop-bound-init

    # -- Public API ---------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether a pipeline run is currently active."""
        return self._current_task is not None and not self._current_task.done()

    @property
    def current_run(self) -> FineTuneRun | None:
        """The in-memory run state.

        After a process restart this is ``None`` until a new run starts;
        use ``get_status()`` for persistent state.
        """
        return self._current_run

    async def start(
        self,
        request: FineTuneRequest,
    ) -> FineTuneRun:
        """Start a new pipeline run.

        Args:
            request: Fine-tuning request parameters.

        Returns:
            The created run record.

        Raises:
            FineTuneRunActiveError: If a run is already active (409 Conflict).
        """
        async with self._op_lock:
            if self.is_running:
                msg = "A fine-tuning run is already active"
                raise FineTuneRunActiveError(msg)

            config = build_config(request)
            now = datetime.now(UTC)
            run = FineTuneRun(
                id=str(uuid.uuid4()),
                stage=FineTuneStage.GENERATING_DATA,
                config=config,
                started_at=now,
                updated_at=now,
            )
            await self._run_repo.save(run)
            self._current_run = run
            logger.info(
                MEMORY_FINE_TUNE_STARTED,
                run_id=run.id,
                source_dir=config.source_dir,
            )

            self._cancellation = CancellationToken()
            self._current_task = asyncio.create_task(
                self._execute_pipeline(run),
            )
            self._current_task.add_done_callback(self._on_task_done)
            return run

    async def resume(self, run_id: str) -> FineTuneRun:
        """Resume a failed run from the last completed stage.

        Cancelled runs are stored as FAILED and are resumable.
        Completed runs cannot be resumed.

        Args:
            run_id: ID of the run to resume.

        Returns:
            The resumed run record.

        Raises:
            FineTuneRunActiveError: If a run is already active.
            ValueError: If run not found or not resumable.
        """
        async with self._op_lock:
            if self.is_running:
                msg = "A fine-tuning run is already active"
                raise FineTuneRunActiveError(msg)
            run = await self._run_repo.get(run_id)
            if run is None:
                msg = f"Run {run_id} not found"
                raise ValueError(msg)
            if run.stage != FineTuneStage.FAILED:
                msg = f"Run {run_id} is not resumable (stage={run.stage})"
                raise ValueError(msg)

            now = datetime.now(UTC)
            resumed = run.model_copy(
                update={
                    "stage": FineTuneStage.GENERATING_DATA,
                    "progress": None,
                    "error": None,
                    "updated_at": now,
                    "completed_at": None,
                },
            )
            await self._run_repo.save(resumed)
            self._current_run = resumed
            logger.info(
                MEMORY_FINE_TUNE_STARTED,
                run_id=run_id,
                resumed=True,
                stages_completed=run.stages_completed,
            )

            self._cancellation = CancellationToken()
            self._current_task = asyncio.create_task(
                self._execute_pipeline(resumed),
            )
            self._current_task.add_done_callback(self._on_task_done)
            return resumed

    async def cancel(self) -> None:
        """Cancel the active pipeline run and wait for it to stop.

        Signals cooperative cancellation and awaits the background
        task for up to 30 seconds. If the task does not stop in
        time, the method returns anyway.
        """
        async with self._op_lock:
            if self._cancellation is not None:
                self._cancellation.cancel()
                logger.info(MEMORY_FINE_TUNE_CANCELLED)
            task = self._current_task
        # Await outside the lock so pipeline can complete.
        if task is not None and not task.done():
            try:
                async with asyncio.timeout(_CANCEL_TIMEOUT_SEC):
                    await asyncio.shield(task)
            except TimeoutError:
                logger.warning(
                    MEMORY_FINE_TUNE_CANCELLED,
                    note="cancel timed out waiting for task",
                )
            except Exception as exc:
                reraise_critical(exc)
                # Task failed/cancelled -- already logged by _on_task_done

    async def recover_interrupted(self) -> int:
        """Mark interrupted runs as FAILED on startup.

        Returns:
            Result of type ``int``.
        """
        return await self._run_repo.mark_interrupted()

    async def get_status(self) -> FineTuneStatus:
        """Get current pipeline status.

        Returns:
            Result of type ``FineTuneStatus``.
        """
        if self._current_run is not None:
            return FineTuneStatus(
                run_id=self._current_run.id,
                stage=self._current_run.stage,
                progress=self._current_run.progress,
                error=self._current_run.error,
            )
        # Check DB for most recent run.
        runs, _ = await self._run_repo.list_items_page(limit=1)
        if runs:
            r = runs[0]
            return FineTuneStatus(
                run_id=r.id,
                stage=r.stage,
                progress=r.progress,
                error=r.error,
            )
        return FineTuneStatus()

    # -- Pipeline execution -------------------------------------------

    async def _execute_pipeline(self, run: FineTuneRun) -> None:
        """Execute stages sequentially in the background."""
        try:
            run = await self._run_stages(run)
            now = datetime.now(UTC)
            run = run.model_copy(
                update={
                    "stage": FineTuneStage.COMPLETE,
                    "progress": None,
                    "updated_at": now,
                    "completed_at": now,
                },
            )
            await self._run_repo.save(run)
            self._current_run = run
            logger.info(
                MEMORY_FINE_TUNE_COMPLETED,
                run_id=run.id,
            )
            self._schedule_ws(
                "memory.fine_tune.completed",
                run,
            )
        except FineTuneCancelledError:
            logger.info(
                MEMORY_FINE_TUNE_CANCELLED,
                run_id=run.id,
                stage=run.stage.value,
            )
            try:
                await self._mark_failed(
                    self._current_run or run,
                    "cancelled by user",
                )
            except Exception as exc:
                reraise_critical(exc)
                # Update in-memory state even if DB fails. Mirror
                # ``_mark_failed`` so the snapshot has the same terminal
                # shape (progress cleared, timestamps stamped) instead
                # of a stale stage with leftover progress data.
                # Base on the latest snapshot (``self._current_run``)
                # rather than the entry-state ``run`` so a cancellation
                # mid-pipeline does not regress ``stages_completed`` or
                # the current stage if a later stage already updated
                # the in-memory snapshot.
                base = self._current_run or run
                self._current_run = to_failed(
                    base,
                    "cancelled by user",
                    now=datetime.now(UTC),
                )
                logger.warning(
                    MEMORY_FINE_TUNE_FAILED,
                    run_id=run.id,
                    note="failed_to_persist_cancellation_state",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            self._schedule_ws(
                "memory.fine_tune.failed",
                self._current_run or run,
            )
        except Exception as exc:
            reraise_critical(exc)
            safe_error = safe_error_description(exc)
            try:
                await self._mark_failed(self._current_run or run, safe_error)
            except Exception as persist_exc:
                reraise_critical(persist_exc)
                # Persisting the FAILED state can itself fail (DB outage,
                # disk full, etc.). Log the persistence failure with full
                # context, then synthesise the same fully-terminal state
                # ``_mark_failed`` would have produced (stage, progress
                # cleared, error, updated_at, completed_at) on top of the
                # most recent in-memory ``self._current_run`` so
                # ``get_status`` and the WS event don't return a FAILED
                # run with stale progress or missing terminal timestamps.
                now = datetime.now(UTC)
                # The MemoryError/RecursionError carve-out above
                # means persist_exc is guaranteed non-catastrophic
                # at this point, so we deliberately omit
                # ``exc_info=True``: the sanitised structured fields
                # are the only thing that should land in the log
                # record on this path. ``noqa: TRY400`` because
                # ``logger.exception`` would auto-attach a traceback
                # whose frame-locals can carry credentials.
                log_exception_redacted(
                    logger,
                    MEMORY_FINE_TUNE_FAILED,
                    persist_exc,
                    run_id=run.id,
                    stage="persist_failed_state",
                    underlying_error_type=type(exc).__name__,
                    underlying_error=safe_error,
                )
                base = self._current_run or run
                self._current_run = to_failed(base, safe_error, now=now)
            self._schedule_ws(
                "memory.fine_tune.failed",
                self._current_run or run,
            )
            logger.warning(
                MEMORY_FINE_TUNE_FAILED,
                run_id=run.id,
                error_type=type(exc).__name__,
                error=safe_error,
            )

    async def _run_stages(
        self,
        run: FineTuneRun,
    ) -> FineTuneRun:
        """Run all pipeline stages, skipping completed ones (resume).

        Delegates stage sequencing to :func:`run_fine_tune_stages`,
        passing the run-state lifecycle hooks so this orchestrator stays
        the single owner of ``_current_run`` and progress emission.

        Returns:
            Result of type ``FineTuneRun``.
        """
        return await run_fine_tune_stages(
            run,
            checkpoint_repo=self._checkpoint_repo,
            settings_service=self._settings_service,
            training_data_source=self._training_data_source,
            llm_provider=self._llm_provider,
            cancellation=self._cancellation,
            enter_stage=self._enter_stage,
            complete_stage=self._complete_stage,
            make_progress_cb=self._make_progress_cb,
        )

    # -- Stage lifecycle helpers --------------------------------------

    async def _enter_stage(
        self,
        run: FineTuneRun,
        stage: FineTuneStage,
    ) -> FineTuneRun:
        """Mark a stage as entered.

        Returns:
            Result of type ``FineTuneRun``.
        """
        now = datetime.now(UTC)
        run = run.model_copy(
            update={
                "stage": stage,
                "progress": 0.0,
                "updated_at": now,
            },
        )
        await self._run_repo.save(run)
        self._current_run = run
        logger.info(
            MEMORY_FINE_TUNE_STAGE_ENTERED,
            run_id=run.id,
            stage=stage.value,
        )
        self._schedule_ws(
            "memory.fine_tune.stage_changed",
            run,
        )
        return run

    async def _complete_stage(
        self,
        run: FineTuneRun,
        stage_name: str,
    ) -> FineTuneRun:
        """Record a stage as completed.

        Returns:
            Result of type ``FineTuneRun``.
        """
        now = datetime.now(UTC)
        run = run.model_copy(
            update={
                "progress": None,
                "updated_at": now,
                "stages_completed": (
                    *run.stages_completed,
                    stage_name,
                ),
            },
        )
        await self._run_repo.save(run)
        self._current_run = run
        return run

    async def _mark_failed(
        self,
        run: FineTuneRun,
        error: str,
    ) -> None:
        """Mark the run as failed."""
        run = to_failed(run, error, now=datetime.now(UTC))
        await self._run_repo.save(run)
        self._current_run = run

    # -- Progress + WebSocket helpers ---------------------------------

    def _make_progress_cb(
        self,
        run: FineTuneRun,
    ) -> Callable[[float], None]:
        """Create a throttled progress callback for a stage.

        The callback is safe to call from worker threads: it schedules
        state mutations back onto the event loop via
        ``call_soon_threadsafe``.

        Returns:
            A throttled callback accepting a 0.0-1.0 progress fraction.
        """
        run_id = run.id
        run_stage = run.stage
        last_emit = 0.0
        loop = asyncio.get_running_loop()
        # Bind the clock once outside the worker-thread closure so each
        # callback invocation reads through a stable attribute.
        # ``SystemClock.monotonic`` delegates to ``time.monotonic``
        # (thread-safe) so production callbacks invoked from worker
        # threads are correct. Tests that drive ``_cb`` directly must
        # invoke it from the test thread (the FakeClock's ``_now``
        # field is not synchronised); the production stage runners
        # only ever call back from one worker thread per stage.
        clock = self._clock

        def _update_on_loop(progress: float) -> None:
            """Apply progress update (runs on event loop thread)."""
            current = self._current_run
            if current is not None and current.id == run_id:
                if current.stage is not run_stage:
                    # Stale callback from a stage the run has since left;
                    # don't clobber the current stage's progress with it.
                    return
                updated = current.model_copy(
                    update={"progress": progress},
                )
            else:
                updated = run.model_copy(
                    update={"progress": progress},
                )
            self._current_run = updated
            logger.debug(
                MEMORY_FINE_TUNE_PROGRESS,
                run_id=run_id,
                progress=progress,
            )
            self._emit_ws(
                "memory.fine_tune.progress",
                updated,
            )

        def _cb(progress: float) -> None:
            """Throttled progress callback: emit at most once per interval."""
            nonlocal last_emit
            now = clock.monotonic()
            if now - last_emit < _PROGRESS_THROTTLE_SEC:
                return
            last_emit = now
            loop.call_soon_threadsafe(_update_on_loop, progress)

        return _cb

    def _schedule_ws(
        self,
        event_type: str,
        run: FineTuneRun,
    ) -> None:
        """Emit a WS event from the event-loop thread.

        Only ever called from the loop thread (the pipeline coroutine and
        its stage helpers); worker-thread progress callbacks marshal back
        via ``call_soon_threadsafe`` before touching WS state. When no
        loop is running (teardown, or tests driving helpers synchronously)
        the emit is skipped rather than raising.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- nothing to publish onto.
            return
        self._emit_ws(event_type, run)

    def _emit_ws(
        self,
        event_type: str,
        run: FineTuneRun,
    ) -> None:
        """Best-effort emit a WebSocket event."""
        publish_ws_event(self._channels_plugin, event_type, run)

    @staticmethod
    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Log unhandled exceptions from pipeline background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log_exception_redacted(
                logger,
                MEMORY_FINE_TUNE_FAILED,
                exc,
                note="unhandled exception in pipeline task",
            )

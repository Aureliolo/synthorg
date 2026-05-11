"""Fine-tuning pipeline orchestrator.

Manages background execution of the five-stage pipeline with state
persistence, cancellation, WebSocket progress events, and resume
of failed runs from the last completed stage.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from synthorg.core.clock import Clock, SystemClock
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import (
    FineTuneStage,
    contrastive_fine_tune,
    deploy_checkpoint,
    evaluate_checkpoint,
    generate_training_data,
    mine_hard_negatives,
)
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
    FineTuneRunConfig,
    FineTuneStatus,
)
from synthorg.memory.errors import FineTuneCancelledError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_CANCELLED,
    MEMORY_FINE_TUNE_COMPLETED,
    MEMORY_FINE_TUNE_FAILED,
    MEMORY_FINE_TUNE_PROGRESS,
    MEMORY_FINE_TUNE_STAGE_ENTERED,
    MEMORY_FINE_TUNE_STARTED,
    MEMORY_FINE_TUNE_WS_EMIT_FAILED,
)

if TYPE_CHECKING:
    from synthorg.memory.embedding.fine_tune_models import (
        FineTuneRequest,
    )
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
        FineTuneRunRepository,
    )

logger = get_logger(__name__)

# Minimum interval between WS progress events.
_PROGRESS_THROTTLE_SEC = 1.0


class ChannelsPlugin(Protocol):
    """Protocol for WebSocket channel publishing."""

    def publish(self, data: str, *, channels: list[str]) -> None:
        """Publish data to the given channels."""
        ...


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
    """

    def __init__(  # noqa: PLR0913 -- pluggable dependencies threaded for testability
        self,
        *,
        run_repo: FineTuneRunRepository,
        checkpoint_repo: FineTuneCheckpointRepository,
        settings_service: object | None = None,
        channels_plugin: ChannelsPlugin | None = None,
        llm_provider: object | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._checkpoint_repo = checkpoint_repo
        self._settings_service = settings_service
        self._channels_plugin = channels_plugin
        self._llm_provider = llm_provider
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._current_task: asyncio.Task[None] | None = None
        self._cancellation: CancellationToken | None = None
        self._current_run: FineTuneRun | None = None
        # Eager init: ``start_pipeline`` and ``cancel_pipeline`` may
        # interleave; the lock must be present before the first call.
        self._op_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see above.

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
            RuntimeError: If a run is already active (409 Conflict).
        """
        async with self._op_lock:
            if self.is_running:
                msg = "A fine-tuning run is already active"
                raise RuntimeError(msg)

            config = _build_config(request)
            now = datetime.now(UTC)
            run = FineTuneRun(
                id=str(uuid.uuid4()),
                stage=FineTuneStage.GENERATING_DATA,
                config=config,
                started_at=now,
                updated_at=now,
            )
            await self._run_repo.save_run(run)
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
            RuntimeError: If a run is already active.
            ValueError: If run not found or not resumable.
        """
        async with self._op_lock:
            if self.is_running:
                msg = "A fine-tuning run is already active"
                raise RuntimeError(msg)
            run = await self._run_repo.get_run(run_id)
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
            await self._run_repo.save_run(resumed)
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
                async with asyncio.timeout(30):
                    await asyncio.shield(task)
            except TimeoutError:
                logger.warning(
                    MEMORY_FINE_TUNE_CANCELLED,
                    note="cancel timed out waiting for task",
                )
            except MemoryError, RecursionError:
                raise
            except Exception:  # noqa: S110
                pass  # Task failed/cancelled -- already logged by _on_task_done

    async def recover_interrupted(self) -> int:
        """Mark interrupted runs as FAILED on startup."""
        return await self._run_repo.mark_interrupted()

    async def get_status(self) -> FineTuneStatus:
        """Get current pipeline status."""
        if self._current_run is not None:
            return FineTuneStatus(
                run_id=self._current_run.id,
                stage=self._current_run.stage,
                progress=self._current_run.progress,
                error=self._current_run.error,
            )
        # Check DB for most recent run.
        runs, _ = await self._run_repo.list_runs(limit=1)
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
            await self._run_repo.save_run(run)
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
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                # Update in-memory state even if DB fails. Mirror
                # ``_mark_failed`` so the snapshot has the same terminal
                # shape (progress cleared, timestamps stamped) instead
                # of a stale stage with leftover progress data.
                # Base on the latest snapshot (``self._current_run``)
                # rather than the entry-state ``run`` so a cancellation
                # mid-pipeline does not regress ``stages_completed`` or
                # the current stage if a later stage already updated
                # the in-memory snapshot.
                now = datetime.now(UTC)
                base = self._current_run or run
                self._current_run = base.model_copy(
                    update={
                        "stage": FineTuneStage.FAILED,
                        "error": "cancelled by user",
                        "progress": None,
                        "updated_at": now,
                        "completed_at": now,
                    },
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
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            safe_error = safe_error_description(exc)
            try:
                await self._mark_failed(self._current_run or run, safe_error)
            except MemoryError, RecursionError:
                # Catastrophic interpreter state from the persistence
                # layer must propagate; do not absorb into the FAILED
                # fallback path.
                raise
            except Exception as persist_exc:
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
                logger.error(
                    MEMORY_FINE_TUNE_FAILED,
                    run_id=run.id,
                    stage="persist_failed_state",
                    error_type=type(persist_exc).__name__,
                    error=safe_error_description(persist_exc),
                    underlying_error_type=type(exc).__name__,
                    underlying_error=safe_error,
                )
                base = self._current_run or run
                self._current_run = base.model_copy(
                    update={
                        "stage": FineTuneStage.FAILED,
                        "progress": None,
                        "error": safe_error,
                        "updated_at": now,
                        "completed_at": now,
                    },
                )
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
        """Run all stages, skipping completed ones (resume)."""
        cfg = run.config
        out_dir = f"{cfg.output_dir}/runs/{run.id}"
        completed = set(run.stages_completed)

        # Stage 1: Generate training data.
        if "generating_data" not in completed:
            run = await self._enter_stage(
                run,
                FineTuneStage.GENERATING_DATA,
            )
            train_path, val_path = await generate_training_data(
                source_dir=cfg.source_dir,
                output_dir=out_dir,
                llm_provider=self._llm_provider,
                validation_split=cfg.validation_split,
                progress_callback=self._make_progress_cb(run),
                cancellation=self._cancellation,
            )
            run = await self._complete_stage(
                run,
                "generating_data",
            )
        else:
            train_path = Path(f"{out_dir}/training.jsonl")
            val_path = Path(f"{out_dir}/validation.jsonl")

        # Stage 2: Mine hard negatives.
        if "mining_negatives" not in completed:
            run = await self._enter_stage(
                run,
                FineTuneStage.MINING_NEGATIVES,
            )
            triples_path = await mine_hard_negatives(
                training_data_path=str(train_path),
                base_model=cfg.base_model,
                output_dir=out_dir,
                top_k=cfg.top_k,
                progress_callback=self._make_progress_cb(run),
                cancellation=self._cancellation,
            )
            run = await self._complete_stage(
                run,
                "mining_negatives",
            )
        else:
            triples_path = Path(f"{out_dir}/training_triples.jsonl")

        # Stage 3: Contrastive fine-tuning.
        if "training" not in completed:
            run = await self._enter_stage(
                run,
                FineTuneStage.TRAINING,
            )
            checkpoint_path = await contrastive_fine_tune(
                training_data_path=str(triples_path),
                base_model=cfg.base_model,
                output_dir=out_dir,
                epochs=cfg.epochs,
                learning_rate=cfg.learning_rate,
                temperature=cfg.temperature,
                batch_size=cfg.batch_size,
                progress_callback=self._make_progress_cb(run),
                cancellation=self._cancellation,
            )
            run = await self._complete_stage(run, "training")
        else:
            checkpoint_path = Path(f"{out_dir}/checkpoint")

        # Stage 4: Evaluation.
        if "evaluating" not in completed:
            run = await self._enter_stage(
                run,
                FineTuneStage.EVALUATING,
            )
            eval_metrics = await evaluate_checkpoint(
                checkpoint_path=str(checkpoint_path),
                base_model=cfg.base_model,
                validation_data_path=str(val_path),
                output_dir=out_dir,
                progress_callback=self._make_progress_cb(run),
                cancellation=self._cancellation,
            )
            run = await self._complete_stage(run, "evaluating")
        else:
            eval_metrics = None

        # Stage 5: Deploy.
        if "deploying" not in completed:
            run = await self._enter_stage(
                run,
                FineTuneStage.DEPLOYING,
            )
            backup_json = await deploy_checkpoint(
                checkpoint_path=str(checkpoint_path),
                settings_service=self._settings_service,
            )
            # Persist checkpoint record.
            size_bytes = _dir_size(checkpoint_path)
            record = CheckpointRecord(
                id=str(uuid.uuid4()),
                run_id=run.id,
                model_path=str(checkpoint_path),
                base_model=cfg.base_model,
                doc_count=0,
                eval_metrics=eval_metrics,
                size_bytes=size_bytes,
                created_at=datetime.now(UTC),
                is_active=True,
                backup_config_json=backup_json,
            )
            await self._checkpoint_repo.deactivate_all()
            await self._checkpoint_repo.save_checkpoint(record)
            run = await self._complete_stage(run, "deploying")

        return run

    # -- Stage lifecycle helpers --------------------------------------

    async def _enter_stage(
        self,
        run: FineTuneRun,
        stage: FineTuneStage,
    ) -> FineTuneRun:
        """Mark a stage as entered."""
        now = datetime.now(UTC)
        run = run.model_copy(
            update={
                "stage": stage,
                "progress": 0.0,
                "updated_at": now,
            },
        )
        await self._run_repo.save_run(run)
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
        """Record a stage as completed."""
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
        await self._run_repo.save_run(run)
        self._current_run = run
        return run

    async def _mark_failed(
        self,
        run: FineTuneRun,
        error: str,
    ) -> None:
        """Mark the run as failed."""
        now = datetime.now(UTC)
        run = run.model_copy(
            update={
                "stage": FineTuneStage.FAILED,
                "progress": None,
                "error": error,
                "updated_at": now,
                "completed_at": now,
            },
        )
        await self._run_repo.save_run(run)
        self._current_run = run

    # -- Progress + WebSocket helpers ---------------------------------

    def _make_progress_cb(
        self,
        run: FineTuneRun,
    ) -> Any:
        """Create a throttled progress callback for a stage.

        The callback is safe to call from worker threads: it schedules
        state mutations back onto the event loop via
        ``call_soon_threadsafe``.
        """
        run_id = run.id
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
        """Emit a WS event (safe from both event loop and threads)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- skip.
            return
        if asyncio.get_event_loop() is loop:
            self._emit_ws(event_type, run)
        else:
            loop.call_soon_threadsafe(self._emit_ws, event_type, run)

    def _emit_ws(
        self,
        event_type: str,
        run: FineTuneRun,
    ) -> None:
        """Best-effort emit a WebSocket event."""
        if self._channels_plugin is None:
            return
        try:
            payload = json.dumps(
                {
                    "event_type": event_type,
                    "channel": "system",
                    "payload": {
                        "run_id": run.id,
                        "stage": run.stage.value,
                        "progress": run.progress,
                    },
                },
            )
            self._channels_plugin.publish(
                payload,
                channels=["system"],
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                MEMORY_FINE_TUNE_WS_EMIT_FAILED,
                event_type=event_type,
                run_id=run.id,
            )

    @staticmethod
    def _on_task_done(task: asyncio.Task[None]) -> None:
        """Log unhandled exceptions from pipeline background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                MEMORY_FINE_TUNE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="unhandled exception in pipeline task",
            )


# -- Helpers -----------------------------------------------------------


def _dir_size(path: Path) -> int:
    """Compute total size in bytes of a directory."""
    if not path.is_dir():
        return path.stat().st_size if path.exists() else 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _build_config(request: FineTuneRequest) -> FineTuneRunConfig:
    """Build a frozen config snapshot from a request."""
    overrides = {
        k: v
        for k, v in request.model_dump(
            exclude={"resume_run_id"},
        ).items()
        if v is not None
    }
    defaults = {
        "base_model": "all-MiniLM-L6-v2",
        "output_dir": "/data/fine-tune",
        "epochs": 3,
        "learning_rate": 1e-5,
        "temperature": 0.02,
        "top_k": 4,
        "batch_size": 128,
        "validation_split": 0.1,
    }
    merged = {**defaults, **overrides}
    return FineTuneRunConfig(**merged)

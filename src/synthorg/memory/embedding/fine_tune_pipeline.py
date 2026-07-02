# module-kind: orchestrator
"""Sequential stage runner for the fine-tune pipeline.

Runs the five pipeline stages (generate data, mine negatives, train,
evaluate, deploy) in order, skipping any already recorded as complete so
a resumed run picks up where it left off. Stage lifecycle bookkeeping
(persisting entered/completed state, progress callbacks) is delegated
back to the caller via the ``enter_stage`` / ``complete_stage`` /
``make_progress_cb`` hooks, so the orchestrator stays the single owner of
run state while this module owns only the stage sequencing.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import (
    FineTuneStage,
    ProgressCallback,
    deploy_checkpoint,
)
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    EvalMetrics,
    FineTuneExecutionConfig,
    FineTuneRun,
    FineTuneRunConfig,
)
from synthorg.memory.embedding.fine_tune_query import QueryGenerator
from synthorg.memory.embedding.fine_tune_run_helpers import (
    dir_size,
    generate_run_training_data,
)
from synthorg.memory.embedding.fine_tune_stage_dispatch import (
    evaluating_stage_config,
    mining_stage_config,
    training_stage_config,
)
from synthorg.memory.embedding.fine_tune_stage_executor import StageExecutor
from synthorg.memory.embedding.promotion import should_promote_checkpoint
from synthorg.memory.embedding.training_sources import TrainingDataSource
from synthorg.memory.errors import FineTuneStageExecutionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_CHECKPOINT_REJECTED,
    MEMORY_FINE_TUNE_EVAL_METRICS_UNREADABLE,
)
from synthorg.persistence.fine_tune_protocol import (
    FineTuneCheckpointRepository,
)

logger = get_logger(__name__)

type EnterStageFn = Callable[[FineTuneRun, FineTuneStage], Awaitable[FineTuneRun]]
type CompleteStageFn = Callable[[FineTuneRun, str], Awaitable[FineTuneRun]]
type ProgressCbFactory = Callable[[FineTuneRun], ProgressCallback]
# Async so the wiring can resolve hot settings (data volume) per run.
type StageExecutorFactory = Callable[
    [FineTuneExecutionConfig | None], Awaitable[StageExecutor]
]


@dataclass(frozen=True)
class _PipelineHooks:
    """Run-state bookkeeping callbacks owned by the orchestrator."""

    enter_stage: EnterStageFn
    complete_stage: CompleteStageFn
    make_progress_cb: ProgressCbFactory


async def _read_eval_metrics(out_dir: str) -> EvalMetrics:
    """Read the metrics file the evaluation stage persisted.

    Both executors write ``eval_metrics.json`` at a deterministic path
    (in-process and in-container via the shared ``/data`` volume), so
    reading it back is the one metrics channel that works across the
    container boundary.

    Returns:
        Result of type ``EvalMetrics``.

    Raises:
        FineTuneStageExecutionError: When the file is missing or
            invalid; for a docker-backed run that usually means the
            configured data volume is not the one the backend mounts.
    """
    metrics_path = Path(out_dir) / "eval_metrics.json"
    try:
        raw = await asyncio.to_thread(metrics_path.read_text, encoding="utf-8")
        return EvalMetrics.model_validate_json(raw)
    except (OSError, ValueError) as exc:
        reraise_critical(exc)
        logger.warning(
            MEMORY_FINE_TUNE_EVAL_METRICS_UNREADABLE,
            metrics_path=str(metrics_path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"evaluation metrics unreadable at {metrics_path}:"
            f" {safe_error_description(exc)}. For a docker-backed run,"
            " verify memory.fine_tune_data_volume names the same volume"
            " the backend mounts at /data."
        )
        raise FineTuneStageExecutionError(msg) from exc


async def _run_torch_stage(  # noqa: PLR0913 -- stage collaborators threaded explicitly
    run: FineTuneRun,
    *,
    executor: StageExecutor,
    stage: FineTuneStage,
    stage_key: str,
    config: dict[str, object],
    hooks: _PipelineHooks,
    cancellation: CancellationToken | None,
) -> FineTuneRun:
    """Enter, execute, and complete one torch-bound stage.

    Returns:
        Result of type ``FineTuneRun``.
    """
    run = await hooks.enter_stage(run, stage)
    await executor.run_stage(
        stage=stage,
        config=config,
        run_id=str(run.id),
        progress_callback=hooks.make_progress_cb(run),
        cancellation=cancellation,
    )
    return await hooks.complete_stage(run, stage_key)


async def _generate_stage(  # noqa: PLR0913 -- stage collaborators threaded explicitly
    run: FineTuneRun,
    *,
    out_dir: str,
    completed: set[str],
    hooks: _PipelineHooks,
    training_data_source: TrainingDataSource | None,
    query_generator: QueryGenerator | None,
    cancellation: CancellationToken | None,
) -> tuple[FineTuneRun, Path, Path]:
    """Stage 1: generate training data (or resume its known outputs).

    Returns:
        The run plus the training / validation data paths.
    """
    if "generating_data" in completed:
        return run, Path(out_dir) / "training.jsonl", Path(out_dir) / "validation.jsonl"
    run = await hooks.enter_stage(run, FineTuneStage.GENERATING_DATA)
    train_path, val_path = await generate_run_training_data(
        run.config,
        out_dir,
        training_data_source=training_data_source,
        query_generator=query_generator,
        progress_callback=hooks.make_progress_cb(run),
        cancellation=cancellation,
    )
    run = await hooks.complete_stage(run, "generating_data")
    return run, train_path, val_path


async def _evaluate_stage(  # noqa: PLR0913 -- stage collaborators threaded explicitly
    run: FineTuneRun,
    *,
    executor: StageExecutor,
    out_dir: str,
    checkpoint_path: Path,
    val_path: Path,
    completed: set[str],
    hooks: _PipelineHooks,
    cancellation: CancellationToken | None,
) -> tuple[FineTuneRun, EvalMetrics | None]:
    """Stage 4: evaluate, re-running when deploy is still pending.

    Evaluation is cheap relative to training, so a resume that has not
    deployed yet re-measures rather than trusting stale metrics; once
    deploy finished the metrics are unused.

    Returns:
        The run plus the metrics feeding the promotion gate.
    """
    eval_config = evaluating_stage_config(
        run.config,
        out_dir=out_dir,
        checkpoint_path=str(checkpoint_path),
        val_path=str(val_path),
    )
    if "evaluating" not in completed:
        run = await hooks.enter_stage(run, FineTuneStage.EVALUATING)
        await executor.run_stage(
            stage=FineTuneStage.EVALUATING,
            config=eval_config,
            run_id=str(run.id),
            progress_callback=hooks.make_progress_cb(run),
            cancellation=cancellation,
        )
        eval_metrics = await _read_eval_metrics(out_dir)
        run = await hooks.complete_stage(run, "evaluating")
        return run, eval_metrics
    if "deploying" not in completed:
        await executor.run_stage(
            stage=FineTuneStage.EVALUATING,
            config=eval_config,
            run_id=str(run.id),
            progress_callback=hooks.make_progress_cb(run),
            cancellation=cancellation,
        )
        return run, await _read_eval_metrics(out_dir)
    return run, None


async def _deploy_stage(  # noqa: PLR0913 -- stage collaborators threaded explicitly
    run: FineTuneRun,
    *,
    cfg: FineTuneRunConfig,
    checkpoint_path: Path,
    eval_metrics: EvalMetrics | None,
    checkpoint_repo: FineTuneCheckpointRepository,
    settings_service: object | None,
    hooks: _PipelineHooks,
) -> FineTuneRun:
    """Stage 5: promote ONLY on a measured win.

    A tie or regression records the evaluated checkpoint inactive and
    leaves the live embedder config untouched.

    Returns:
        Result of type ``FineTuneRun``.
    """
    run = await hooks.enter_stage(run, FineTuneStage.DEPLOYING)
    promote = should_promote_checkpoint(eval_metrics)
    backup_json: str | None = None
    if promote:
        backup_json = await deploy_checkpoint(
            checkpoint_path=str(checkpoint_path),
            settings_service=settings_service,
        )
        await checkpoint_repo.deactivate_all()
    else:
        logger.info(
            MEMORY_FINE_TUNE_CHECKPOINT_REJECTED,
            run_id=str(run.id),
            checkpoint_path=str(checkpoint_path),
            metrics_available=eval_metrics is not None,
            base_ndcg_at_10=(eval_metrics.base_ndcg_at_10 if eval_metrics else None),
            ndcg_at_10=(eval_metrics.ndcg_at_10 if eval_metrics else None),
        )
    # Persist checkpoint record (active only on a promote).
    size_bytes = await asyncio.to_thread(dir_size, checkpoint_path)
    record = CheckpointRecord(
        id=uuid.uuid4(),
        run_id=str(run.id),
        model_path=str(checkpoint_path),
        base_model=cfg.base_model,
        doc_count=0,
        eval_metrics=eval_metrics,
        size_bytes=size_bytes,
        created_at=datetime.now(UTC),
        is_active=promote,
        backup_config_json=backup_json,
    )
    await checkpoint_repo.save(record)
    return await hooks.complete_stage(run, "deploying")


async def run_fine_tune_stages(  # noqa: PLR0913 -- pipeline collaborators threaded explicitly
    run: FineTuneRun,
    *,
    checkpoint_repo: FineTuneCheckpointRepository,
    settings_service: object | None,
    training_data_source: TrainingDataSource | None,
    query_generator: QueryGenerator | None,
    cancellation: CancellationToken | None,
    enter_stage: EnterStageFn,
    complete_stage: CompleteStageFn,
    make_progress_cb: ProgressCbFactory,
    make_stage_executor: StageExecutorFactory,
) -> FineTuneRun:
    """Run all stages, skipping completed ones (resume).

    Returns:
        Result of type ``FineTuneRun``.
    """
    cfg = run.config
    # ``output_dir`` is contractually POSIX (``FineTuneRequest`` rejects drive
    # letters); ``PurePosixPath`` joins the run subpath without emitting Windows
    # separators, keeping the ``out_dir`` string the stage runners receive
    # POSIX regardless of host platform.
    out_dir = str(PurePosixPath(cfg.output_dir) / "runs" / str(run.id))
    completed = set(run.stages_completed)
    hooks = _PipelineHooks(enter_stage, complete_stage, make_progress_cb)
    # Torch-bound stages (2-4) go through the executor; stage 1 holds
    # DB/LLM handles and stage 5 touches settings + persistence, so both
    # always run in-process regardless of the execution backend.
    executor = await make_stage_executor(cfg.execution)

    run, train_path, val_path = await _generate_stage(
        run,
        out_dir=out_dir,
        completed=completed,
        hooks=hooks,
        training_data_source=training_data_source,
        query_generator=query_generator,
        cancellation=cancellation,
    )

    # Stage outputs land at deterministic paths under ``out_dir`` (the
    # resume branches always relied on this), which is what lets the
    # executor seam skip return values entirely.
    triples_path = Path(out_dir) / "training_triples.jsonl"
    checkpoint_path = Path(out_dir) / "checkpoint"

    if "mining_negatives" not in completed:
        run = await _run_torch_stage(
            run,
            executor=executor,
            stage=FineTuneStage.MINING_NEGATIVES,
            stage_key="mining_negatives",
            config=mining_stage_config(
                cfg, out_dir=out_dir, train_path=str(train_path)
            ),
            hooks=hooks,
            cancellation=cancellation,
        )

    if "training" not in completed:
        run = await _run_torch_stage(
            run,
            executor=executor,
            stage=FineTuneStage.TRAINING,
            stage_key="training",
            config=training_stage_config(
                cfg, out_dir=out_dir, triples_path=str(triples_path)
            ),
            hooks=hooks,
            cancellation=cancellation,
        )

    run, eval_metrics = await _evaluate_stage(
        run,
        executor=executor,
        out_dir=out_dir,
        checkpoint_path=checkpoint_path,
        val_path=val_path,
        completed=completed,
        hooks=hooks,
        cancellation=cancellation,
    )

    if "deploying" not in completed:
        run = await _deploy_stage(
            run,
            cfg=cfg,
            checkpoint_path=checkpoint_path,
            eval_metrics=eval_metrics,
            checkpoint_repo=checkpoint_repo,
            settings_service=settings_service,
            hooks=hooks,
        )

    return run

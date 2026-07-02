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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

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
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_CHECKPOINT_REJECTED,
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


async def _read_eval_metrics(out_dir: str) -> EvalMetrics:
    """Read the metrics file the evaluation stage persisted.

    Both executors write ``eval_metrics.json`` at a deterministic path
    (in-process and in-container via the shared ``/data`` volume), so
    reading it back is the one metrics channel that works across the
    container boundary.

    Returns:
        Result of type ``EvalMetrics``.
    """
    metrics_path = Path(out_dir) / "eval_metrics.json"
    raw = await asyncio.to_thread(metrics_path.read_text, encoding="utf-8")
    return EvalMetrics.model_validate_json(raw)


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
    # Torch-bound stages (2-4) go through the executor; stage 1 holds
    # DB/LLM handles and stage 5 touches settings + persistence, so both
    # always run in-process regardless of the execution backend.
    executor = await make_stage_executor(cfg.execution)

    # Stage 1: Generate training data (directory scan or real-trajectory
    # harvest, selected by the run's data_source).
    if "generating_data" not in completed:
        run = await enter_stage(run, FineTuneStage.GENERATING_DATA)
        train_path, val_path = await generate_run_training_data(
            cfg,
            out_dir,
            training_data_source=training_data_source,
            query_generator=query_generator,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "generating_data")
    else:
        train_path = Path(out_dir) / "training.jsonl"
        val_path = Path(out_dir) / "validation.jsonl"

    # Stage outputs land at deterministic paths under ``out_dir`` (the
    # resume branches always relied on this), which is what lets the
    # executor seam skip return values entirely.
    triples_path = Path(out_dir) / "training_triples.jsonl"
    checkpoint_path = Path(out_dir) / "checkpoint"

    # Stage 2: Mine hard negatives.
    if "mining_negatives" not in completed:
        run = await enter_stage(run, FineTuneStage.MINING_NEGATIVES)
        await executor.run_stage(
            stage=FineTuneStage.MINING_NEGATIVES,
            config=mining_stage_config(
                cfg, out_dir=out_dir, train_path=str(train_path)
            ),
            run_id=str(run.id),
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "mining_negatives")

    # Stage 3: Contrastive fine-tuning.
    if "training" not in completed:
        run = await enter_stage(run, FineTuneStage.TRAINING)
        await executor.run_stage(
            stage=FineTuneStage.TRAINING,
            config=training_stage_config(
                cfg, out_dir=out_dir, triples_path=str(triples_path)
            ),
            run_id=str(run.id),
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "training")

    # Stage 4: Evaluation. Re-run when deploy is still pending so the
    # promotion gate always has a fresh measured A/B (evaluation is cheap
    # relative to training); on a resume where deploy already finished the
    # metrics are unused.
    eval_config = evaluating_stage_config(
        cfg,
        out_dir=out_dir,
        checkpoint_path=str(checkpoint_path),
        val_path=str(val_path),
    )
    if "evaluating" not in completed:
        run = await enter_stage(run, FineTuneStage.EVALUATING)
        await executor.run_stage(
            stage=FineTuneStage.EVALUATING,
            config=eval_config,
            run_id=str(run.id),
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        eval_metrics = await _read_eval_metrics(out_dir)
        run = await complete_stage(run, "evaluating")
    elif "deploying" not in completed:
        await executor.run_stage(
            stage=FineTuneStage.EVALUATING,
            config=eval_config,
            run_id=str(run.id),
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        eval_metrics = await _read_eval_metrics(out_dir)
    else:
        eval_metrics = None

    # Stage 5: Deploy -- promote ONLY on a measured win. A tie or
    # regression records the evaluated checkpoint inactive and leaves the
    # live embedder config untouched.
    if "deploying" not in completed:
        run = await enter_stage(run, FineTuneStage.DEPLOYING)
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
                base_ndcg_at_10=(
                    eval_metrics.base_ndcg_at_10 if eval_metrics else None
                ),
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
        run = await complete_stage(run, "deploying")

    return run

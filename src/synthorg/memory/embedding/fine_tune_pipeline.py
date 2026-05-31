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
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import (
    FineTuneStage,
    ProgressCallback,
    contrastive_fine_tune,
    deploy_checkpoint,
    evaluate_checkpoint,
    mine_hard_negatives,
)
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    FineTuneRun,
)
from synthorg.memory.embedding.fine_tune_run_helpers import (
    dir_size,
    generate_run_training_data,
)
from synthorg.memory.embedding.promotion import should_promote_checkpoint
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_CHECKPOINT_REJECTED,
)

if TYPE_CHECKING:
    from synthorg.memory.embedding.training_sources import TrainingDataSource
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
    )

logger = get_logger(__name__)

type EnterStageFn = Callable[[FineTuneRun, FineTuneStage], Awaitable[FineTuneRun]]
type CompleteStageFn = Callable[[FineTuneRun, str], Awaitable[FineTuneRun]]
type ProgressCbFactory = Callable[[FineTuneRun], ProgressCallback]


async def run_fine_tune_stages(  # noqa: PLR0913 -- pipeline collaborators threaded explicitly
    run: FineTuneRun,
    *,
    checkpoint_repo: FineTuneCheckpointRepository,
    settings_service: object | None,
    training_data_source: TrainingDataSource | None,
    llm_provider: object | None,
    cancellation: CancellationToken | None,
    enter_stage: EnterStageFn,
    complete_stage: CompleteStageFn,
    make_progress_cb: ProgressCbFactory,
) -> FineTuneRun:
    """Run all stages, skipping completed ones (resume).

    Returns:
        Result of type ``FineTuneRun``.
    """
    cfg = run.config
    out_dir = f"{cfg.output_dir}/runs/{run.id}"
    completed = set(run.stages_completed)

    # Stage 1: Generate training data (directory scan or real-trajectory
    # harvest, selected by the run's data_source).
    if "generating_data" not in completed:
        run = await enter_stage(run, FineTuneStage.GENERATING_DATA)
        train_path, val_path = await generate_run_training_data(
            cfg,
            out_dir,
            training_data_source=training_data_source,
            llm_provider=llm_provider,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "generating_data")
    else:
        train_path = Path(f"{out_dir}/training.jsonl")
        val_path = Path(f"{out_dir}/validation.jsonl")

    # Stage 2: Mine hard negatives.
    if "mining_negatives" not in completed:
        run = await enter_stage(run, FineTuneStage.MINING_NEGATIVES)
        triples_path = await mine_hard_negatives(
            training_data_path=str(train_path),
            base_model=cfg.base_model,
            output_dir=out_dir,
            top_k=cfg.top_k,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "mining_negatives")
    else:
        triples_path = Path(f"{out_dir}/training_triples.jsonl")

    # Stage 3: Contrastive fine-tuning.
    if "training" not in completed:
        run = await enter_stage(run, FineTuneStage.TRAINING)
        checkpoint_path = await contrastive_fine_tune(
            training_data_path=str(triples_path),
            base_model=cfg.base_model,
            output_dir=out_dir,
            epochs=cfg.epochs,
            learning_rate=cfg.learning_rate,
            temperature=cfg.temperature,
            batch_size=cfg.batch_size,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "training")
    else:
        checkpoint_path = Path(f"{out_dir}/checkpoint")

    # Stage 4: Evaluation. Re-run when deploy is still pending so the
    # promotion gate always has a fresh measured A/B (evaluation is cheap
    # relative to training); on a resume where deploy already finished the
    # metrics are unused.
    if "evaluating" not in completed:
        run = await enter_stage(run, FineTuneStage.EVALUATING)
        eval_metrics = await evaluate_checkpoint(
            checkpoint_path=str(checkpoint_path),
            base_model=cfg.base_model,
            validation_data_path=str(val_path),
            output_dir=out_dir,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
        run = await complete_stage(run, "evaluating")
    elif "deploying" not in completed:
        eval_metrics = await evaluate_checkpoint(
            checkpoint_path=str(checkpoint_path),
            base_model=cfg.base_model,
            validation_data_path=str(val_path),
            output_dir=out_dir,
            progress_callback=make_progress_cb(run),
            cancellation=cancellation,
        )
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
                run_id=run.id,
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
            id=str(uuid.uuid4()),
            run_id=run.id,
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

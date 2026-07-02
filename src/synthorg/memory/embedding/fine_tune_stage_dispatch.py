# module-kind: code
"""Shared dispatch for the fine-tune pipeline's torch-bound stages.

Single source of the flat stage-config contract that crosses the
container boundary: the pipeline builds the per-stage dict with the
``*_stage_config`` builders, and both the in-process executor and the
container runner execute it via :func:`dispatch_stage`. Keeping build
and consume in one module means the config the docker executor emits
always matches what the runner reads.

Only the torch-bound stages (mine negatives, train, evaluate) are
dispatchable here: data generation holds DB/LLM handles and deploy
touches settings + persistence, so both always run in-process.
"""

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage, ProgressCallback
from synthorg.memory.embedding.fine_tune_models import FineTuneRunConfig

CONTAINER_STAGES: frozenset[FineTuneStage] = frozenset(
    {
        FineTuneStage.MINING_NEGATIVES,
        FineTuneStage.TRAINING,
        FineTuneStage.EVALUATING,
    }
)


def mining_stage_config(
    cfg: FineTuneRunConfig,
    *,
    out_dir: str,
    train_path: str,
) -> dict[str, object]:
    """Build the stage-config dict for hard-negative mining.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "stage": FineTuneStage.MINING_NEGATIVES.value,
        "training_data_path": train_path,
        "base_model": cfg.base_model,
        "output_dir": out_dir,
        "top_k": cfg.top_k,
    }


def training_stage_config(
    cfg: FineTuneRunConfig,
    *,
    out_dir: str,
    triples_path: str,
) -> dict[str, object]:
    """Build the stage-config dict for contrastive training.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "stage": FineTuneStage.TRAINING.value,
        "training_data_path": triples_path,
        "base_model": cfg.base_model,
        "output_dir": out_dir,
        "epochs": cfg.epochs,
        "learning_rate": cfg.learning_rate,
        "temperature": cfg.temperature,
        "batch_size": cfg.batch_size,
    }


def evaluating_stage_config(
    cfg: FineTuneRunConfig,
    *,
    out_dir: str,
    checkpoint_path: str,
    val_path: str,
) -> dict[str, object]:
    """Build the stage-config dict for checkpoint evaluation.

    Returns:
        Result of type ``dict[str, object]``.
    """
    return {
        "stage": FineTuneStage.EVALUATING.value,
        "checkpoint_path": checkpoint_path,
        "base_model": cfg.base_model,
        "validation_data_path": val_path,
        "output_dir": out_dir,
    }


async def dispatch_stage(
    stage: FineTuneStage,
    config: dict[str, object],
    cancellation: CancellationToken | None,
    *,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Execute one torch-bound stage from its flat config dict.

    Stage outputs land at deterministic paths under the config's
    ``output_dir`` (``training_triples.jsonl``, ``checkpoint/``,
    ``eval_metrics.json``), so callers never need a return value.

    Args:
        stage: The pipeline stage to execute.
        config: Flat stage config built by the ``*_stage_config``
            builders; every consumed key is coerced at the call site
            because the dict may round-trip through JSON.
        cancellation: Checked between work units inside each stage.
        progress_callback: Called with progress 0.0-1.0.

    Raises:
        ValueError: If ``stage`` is not container-dispatchable.
        KeyError: If required config keys are missing for the stage.
    """
    # Lazy imports -- only load ML deps when actually running a stage.
    from synthorg.memory.embedding.fine_tune import (  # noqa: PLC0415
        contrastive_fine_tune,
        evaluate_checkpoint,
        mine_hard_negatives,
    )

    match stage:
        case FineTuneStage.MINING_NEGATIVES:
            await mine_hard_negatives(
                training_data_path=str(config["training_data_path"]),
                base_model=str(config["base_model"]),
                output_dir=str(config["output_dir"]),
                top_k=int(str(config["top_k"])),
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        case FineTuneStage.TRAINING:
            await contrastive_fine_tune(
                training_data_path=str(config["training_data_path"]),
                base_model=str(config["base_model"]),
                output_dir=str(config["output_dir"]),
                epochs=int(str(config["epochs"])),
                learning_rate=float(str(config["learning_rate"])),
                temperature=float(str(config["temperature"])),
                batch_size=int(str(config["batch_size"])),
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        case FineTuneStage.EVALUATING:
            await evaluate_checkpoint(
                checkpoint_path=str(config["checkpoint_path"]),
                base_model=str(config["base_model"]),
                validation_data_path=str(config["validation_data_path"]),
                output_dir=str(config["output_dir"]),
                progress_callback=progress_callback,
                cancellation=cancellation,
            )
        case _:
            msg = f"stage {stage.value!r} is not container-dispatchable"
            raise ValueError(msg)

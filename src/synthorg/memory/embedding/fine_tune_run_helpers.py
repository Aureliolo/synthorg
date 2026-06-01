"""Stateless support helpers for the fine-tune orchestrator.

Split from :mod:`synthorg.memory.embedding.fine_tune_orchestrator` to keep
that module under its size budget. These functions carry no orchestrator
state: training-data generation dispatch (directory scan vs real-trajectory
harvest), checkpoint directory sizing, and the request -> run-config snapshot.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import (
    ProgressCallback,
    generate_training_data,
)
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneDataSourceType,
    FineTuneRequest,
    FineTuneRunConfig,
)
from synthorg.memory.embedding.training_writer import split_and_write_pairs
from synthorg.memory.errors import FineTuneDataSourceError

if TYPE_CHECKING:
    from synthorg.memory.embedding.training_sources import TrainingDataSource

_DEFAULT_BASE_MODEL: Final[str] = "all-MiniLM-L6-v2"
_DEFAULT_OUTPUT_DIR: Final[str] = "/data/fine-tune"


async def generate_run_training_data(  # noqa: PLR0913 -- deps threaded for testability
    cfg: FineTuneRunConfig,
    out_dir: str,
    *,
    training_data_source: TrainingDataSource | None,
    llm_provider: object | None = None,
    progress_callback: ProgressCallback | None = None,
    cancellation: CancellationToken | None = None,
) -> tuple[Path, Path]:
    """Produce stage-1 train/validation files for the run's source mode.

    Returns:
        ``(training_path, validation_path)``.

    Raises:
        FineTuneDataSourceError: If trajectory mode is selected without a
            wired data source.
        ValueError: If directory mode is selected without a ``source_dir``
            (guarded upstream by the config validator).
    """
    if cfg.data_source is FineTuneDataSourceType.TRAJECTORY:
        if training_data_source is None:
            msg = "trajectory data source selected but none is wired"
            raise FineTuneDataSourceError(msg)
        # The harvest can be long (it sweeps the org's whole working
        # history); the source checks the token before its opening queries
        # and inside every per-record loop, so a cancel request interrupts it
        # promptly rather than only at this boundary.
        pairs = await training_data_source.collect(cancellation)
        records = [
            {
                "query": str(pair.query),
                "positive_passage": str(pair.positive_passage),
            }
            for pair in pairs
        ]
        return await split_and_write_pairs(
            records,
            out_dir,
            validation_split=cfg.validation_split,
        )
    if cfg.source_dir is None:
        msg = "source_dir is required in directory mode"
        raise ValueError(msg)
    return await generate_training_data(
        source_dir=cfg.source_dir,
        output_dir=out_dir,
        llm_provider=llm_provider,
        validation_split=cfg.validation_split,
        progress_callback=progress_callback,
        cancellation=cancellation,
    )


def dir_size(path: Path) -> int:
    """Compute total size in bytes of a directory.

    Returns:
        Result of type ``int``.
    """
    if not path.is_dir():
        return path.stat().st_size if path.exists() else 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def build_config(request: FineTuneRequest) -> FineTuneRunConfig:
    """Build a frozen config snapshot from a request.

    Returns:
        Result of type ``FineTuneRunConfig``.
    """
    overrides = {
        k: v
        for k, v in request.model_dump(
            exclude={"resume_run_id"},
        ).items()
        if v is not None
    }
    # base_model + output_dir are the only FineTuneRunConfig fields without
    # their own default; every numeric knob defaults on the model itself, so
    # restating those values here would just risk the two copies drifting.
    defaults = {
        "base_model": _DEFAULT_BASE_MODEL,
        "output_dir": _DEFAULT_OUTPUT_DIR,
    }
    return FineTuneRunConfig(**{**defaults, **overrides})

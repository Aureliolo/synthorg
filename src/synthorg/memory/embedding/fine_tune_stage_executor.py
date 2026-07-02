# module-kind: code
"""Execution seam for the fine-tune pipeline's torch-bound stages.

The pipeline routes hard-negative mining, training, and evaluation
through a :class:`StageExecutor` so the same sequencing code serves
both execution backends: in-process (lazy torch import inside the
backend, bare-metal installs) and docker (ephemeral one-shot container
per stage, CLI-managed installs).
"""

from typing import Protocol, runtime_checkable

from synthorg.memory.embedding.cancellation import CancellationToken
from synthorg.memory.embedding.fine_tune import FineTuneStage, ProgressCallback
from synthorg.memory.embedding.fine_tune_stage_dispatch import dispatch_stage


@runtime_checkable
class StageExecutor(Protocol):
    """Executes one torch-bound pipeline stage to completion."""

    async def run_stage(
        self,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Run *stage* from its flat config dict; outputs land on disk.

        Raises:
            FineTuneCancelledError: When cancellation fired mid-stage.
            FineTuneStageExecutionError: When the stage failed.
        """
        ...


class InProcessStageExecutor:
    """Runs torch-bound stages inside the backend process."""

    async def run_stage(
        self,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Run *stage* via the shared dispatch (lazy torch import)."""
        await dispatch_stage(
            stage,
            config,
            cancellation,
            progress_callback=progress_callback,
        )

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
from synthorg.memory.embedding.fine_tune_docker_runner import (
    FineTuneContainerRunner,
)
from synthorg.memory.embedding.fine_tune_models import FineTuneExecutionConfig
from synthorg.memory.embedding.fine_tune_stage_dispatch import dispatch_stage
from synthorg.memory.errors import FineTuneStageExecutionError


@runtime_checkable
class StageExecutor(Protocol):
    """Executes one torch-bound pipeline stage to completion."""

    async def run_stage(
        self,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        run_id: str,
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
        run_id: str,  # noqa: ARG002 -- protocol shape; labels are docker-only
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


class DockerStageExecutor:
    """Runs torch-bound stages in ephemeral one-shot containers.

    Args:
        execution: The run's baked execution config (image, GPU,
            memory limit, per-stage timeout); ``backend`` must be
            ``"docker"`` with a non-empty image.
        runner: The container launcher (opens a Docker client per stage).
        data_volume: Named volume mounted read-write at ``/data`` in
            every stage container.
    """

    def __init__(
        self,
        *,
        execution: FineTuneExecutionConfig,
        runner: FineTuneContainerRunner,
        data_volume: str,
    ) -> None:
        if execution.backend != "docker" or not execution.image:
            msg = "DockerStageExecutor requires backend='docker' with an image"
            raise FineTuneStageExecutionError(msg)
        self._execution = execution
        self._runner = runner
        self._data_volume = data_volume

    async def run_stage(
        self,
        *,
        stage: FineTuneStage,
        config: dict[str, object],
        run_id: str,
        progress_callback: ProgressCallback | None,
        cancellation: CancellationToken | None,
    ) -> None:
        """Run *stage* in an ephemeral container (removed on exit)."""
        await self._runner.run_stage(
            stage=stage,
            config=config,
            execution=self._execution,
            data_volume=self._data_volume,
            run_id=run_id,
            progress_callback=progress_callback,
            cancellation=cancellation,
        )

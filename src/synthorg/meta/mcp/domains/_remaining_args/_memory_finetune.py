"""Memory-domain MCP args.

Covers fine-tuning, checkpoints, embedder, memory-entry delete.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

FineTuneBackend = Literal["in-process", "docker"]
FineTuneDataSource = Literal["directory", "trajectory"]


class FineTuneExecutionConfig(_ArgsBase):
    """Optional runner-backend execution config for fine-tune tools.

    The ``image is required when backend == 'docker'`` cross-field
    constraint is enforced inside the Pydantic model in
    ``synthorg.memory.fine_tune_plan.FineTuneExecutionConfig``;
    we re-state the static shape here for the wire boundary.
    """

    backend: FineTuneBackend = Field(
        default="in-process",
        description="Execution backend",
    )
    image: NotBlankStr | None = Field(
        default=None,
        description="Container image (required when backend='docker')",
    )
    gpu_enabled: bool = Field(
        default=False,
        description="Request GPU passthrough (docker backend only)",
    )
    memory_limit: NotBlankStr = Field(
        default="8g",
        description="Container memory limit (Docker format)",
    )
    timeout_seconds: float = Field(
        default=7200.0,
        gt=0.0,
        description="Maximum wall-clock time for a single stage",
    )

    @model_validator(mode="after")
    def _docker_requires_image(self) -> Self:
        """Reject ``backend='docker'`` without an ``image``.

        Mirrors the validator on the canonical
        :class:`synthorg.memory.fine_tune_plan.FineTuneExecutionConfig`
        so the wire boundary catches the bad shape at parse time
        instead of relying on the handler-side re-validation.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.backend == "docker" and self.image is None:
            msg = "image is required when backend='docker'"
            raise ValueError(msg)
        return self


class _FineTunePlanFields(_ArgsBase):
    """Shared shape for ``memory.start_fine_tune`` / ``run_preflight``."""

    data_source: FineTuneDataSource = Field(
        default="directory",
        description=(
            "Where training pairs are drawn from: 'directory' (scan"
            " source_dir) or 'trajectory' (harvest org working history)"
        ),
    )
    source_dir: NotBlankStr | None = Field(
        default=None,
        description=(
            "Directory containing org documents (required in directory mode,"
            " ignored in trajectory mode)"
        ),
    )
    base_model: NotBlankStr | None = Field(
        default=None,
        description="Base model to fine-tune (None = active model)",
    )
    output_dir: NotBlankStr | None = Field(
        default=None,
        description="Checkpoint output directory (None = default)",
    )
    resume_run_id: NotBlankStr | None = Field(
        default=None,
        description="Resume a previous failed/cancelled run",
    )
    epochs: int | None = Field(
        default=None, ge=1, description="Override training epochs"
    )
    learning_rate: float | None = Field(
        default=None,
        gt=0.0,
        description="Override learning rate",
    )
    temperature: float | None = Field(
        default=None,
        gt=0.0,
        description="Override InfoNCE temperature",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Override hard negative count per query",
    )
    batch_size: int | None = Field(
        default=None,
        ge=1,
        description="Override training batch size",
    )
    validation_split: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        description="Fraction held out for evaluation",
    )
    execution: FineTuneExecutionConfig | None = Field(
        default=None,
        description="Optional runner-backend execution config",
    )

    @model_validator(mode="after")
    def _require_source_dir_in_directory_mode(self) -> Self:
        """Require ``source_dir`` at the wire boundary in directory mode.

        Mirrors the canonical invariant on
        :class:`synthorg.memory.fine_tune_plan.FineTunePlan` so an MCP
        caller that omits ``source_dir`` in directory mode is rejected
        with an ``invalid_argument`` envelope at the invoker boundary
        rather than reaching the handler-side re-parse.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: If directory mode is selected without a ``source_dir``.
        """
        if self.data_source == "directory" and self.source_dir is None:
            msg = "source_dir is required when data_source is 'directory'"
            raise ValueError(msg)
        return self


class MemoryStartFineTuneArgs(_FineTunePlanFields, AdminGuardrailFields):
    """Args for ``memory.start_fine_tune`` (privileged; requires confirm).

    Starting a run launches the full pipeline -- including the internal
    deploy stage that swaps the active embedding model -- so the same
    ``confirm`` / ``reason`` guardrail the destructive checkpoint ops use
    gates it.
    """


class MemoryResumeFineTuneArgs(AdminGuardrailFields):
    """Args for ``memory.resume_fine_tune`` (privileged; requires confirm)."""

    run_id: NotBlankStr = Field(description="Run ID to resume")


class MemoryGetFineTuneStatusArgs(_ArgsBase):
    """Args for ``memory.get_fine_tune_status``: no fields."""


class MemoryCancelFineTuneArgs(AdminGuardrailFields):
    """Args for ``memory.cancel_fine_tune`` (destructive)."""


class MemoryRunPreflightArgs(_FineTunePlanFields):
    """Args for ``memory.run_preflight``."""


class MemoryListCheckpointsArgs(PaginationFields):
    """Args for ``memory.list_checkpoints``."""


class _CheckpointIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``checkpoint_id``."""

    checkpoint_id: NotBlankStr = Field(description="Checkpoint UUID")


class MemoryDeployCheckpointArgs(_CheckpointIdArgs, AdminGuardrailFields):
    """Args for ``memory.deploy_checkpoint`` (privileged; requires confirm).

    Deploying swaps the active embedding model for all future retrieval,
    so it carries the same ``confirm`` / ``reason`` guardrail as its
    inverse, ``rollback_checkpoint``.
    """


class MemoryRollbackCheckpointArgs(_CheckpointIdArgs, AdminGuardrailFields):
    """Args for ``memory.rollback_checkpoint`` (destructive)."""


class MemoryDeleteCheckpointArgs(_CheckpointIdArgs, AdminGuardrailFields):
    """Args for ``memory.delete_checkpoint`` (destructive)."""


class MemoryListRunsArgs(PaginationFields):
    """Args for ``memory.list_runs``."""


class MemoryGetActiveEmbedderArgs(_ArgsBase):
    """Args for ``memory.get_active_embedder``: no fields."""


class MemoryDeleteEntryArgs(AdminGuardrailFields):
    """Args for ``memory.delete_entry`` (destructive)."""

    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    memory_id: NotBlankStr = Field(description="Backend-assigned memory identifier")

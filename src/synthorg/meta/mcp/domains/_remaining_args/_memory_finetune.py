"""Memory-domain MCP args.

Covers fine-tuning, checkpoints, embedder, GDPR delete.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    DestructiveGuardrailFields,
    PaginationFields,
    _ArgsBase,
)

FineTuneBackend = Literal["in-process", "docker"]


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
        """
        if self.backend == "docker" and self.image is None:
            msg = "image is required when backend='docker'"
            raise ValueError(msg)
        return self


class _FineTunePlanFields(_ArgsBase):
    """Shared shape for ``memory.start_fine_tune`` / ``run_preflight``."""

    source_dir: NotBlankStr = Field(description="Directory containing org documents")
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


class MemoryStartFineTuneArgs(_FineTunePlanFields):
    """Args for ``memory.start_fine_tune``."""


class MemoryResumeFineTuneArgs(_ArgsBase):
    """Args for ``memory.resume_fine_tune``."""

    run_id: NotBlankStr = Field(description="Run ID to resume")


class MemoryGetFineTuneStatusArgs(_ArgsBase):
    """Args for ``memory.get_fine_tune_status``: no fields."""


class MemoryCancelFineTuneArgs(DestructiveGuardrailFields):
    """Args for ``memory.cancel_fine_tune`` (destructive)."""


class MemoryRunPreflightArgs(_FineTunePlanFields):
    """Args for ``memory.run_preflight``."""


class MemoryListCheckpointsArgs(PaginationFields):
    """Args for ``memory.list_checkpoints``."""


class _CheckpointIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``checkpoint_id``."""

    checkpoint_id: NotBlankStr = Field(description="Checkpoint UUID")


class MemoryDeployCheckpointArgs(_CheckpointIdArgs):
    """Args for ``memory.deploy_checkpoint``."""


class MemoryRollbackCheckpointArgs(_CheckpointIdArgs, DestructiveGuardrailFields):
    """Args for ``memory.rollback_checkpoint`` (destructive)."""


class MemoryDeleteCheckpointArgs(_CheckpointIdArgs, DestructiveGuardrailFields):
    """Args for ``memory.delete_checkpoint`` (destructive)."""


class MemoryListRunsArgs(PaginationFields):
    """Args for ``memory.list_runs``."""


class MemoryGetActiveEmbedderArgs(_ArgsBase):
    """Args for ``memory.get_active_embedder``: no fields."""


class MemoryDeleteEntryArgs(DestructiveGuardrailFields):
    """Args for ``memory.delete_entry`` (destructive, GDPR)."""

    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    memory_id: NotBlankStr = Field(description="Backend-assigned memory identifier")

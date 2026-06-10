"""Domain models for the fine-tuning pipeline."""

from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.memory.embedding.fine_tune import FineTuneStage


def _assert_safe_base_model(value: str | None) -> None:
    """Reject base-model references that could trigger remote-code / SSRF loads.

    The fine-tune backend passes ``base_model`` straight to the embedding
    library's model loader (``SentenceTransformer(base_model)``). A URL scheme
    would let that loader fetch -- and on legacy pickle weight formats, execute
    -- an arbitrary remote artefact, and parent-directory traversal or a Windows
    path would escape the model store. All three are rejected here; Hugging
    Face ``org/name`` identifiers and POSIX local paths pass. This is the
    boundary half of the defence; the call sites also pin
    ``trust_remote_code=False``.

    Raises:
        ValueError: If the reference is a URL, contains parent-directory
            traversal, or uses a backslash / drive letter.
    """
    if value is None:
        return
    if "://" in value:
        msg = "base_model must be a model id or POSIX path, not a URL"
        raise ValueError(msg)
    parts = PureWindowsPath(value).parts + PurePosixPath(value).parts
    if ".." in parts:
        msg = "base_model must not contain parent-directory traversal (..)"
        raise ValueError(msg)
    if "\\" in value or (len(value) >= 2 and value[1] == ":"):  # noqa: PLR2004
        msg = (
            "base_model must be a POSIX path or model id "
            "(no backslashes or drive letters)"
        )
        raise ValueError(msg)


class FineTuneDataSourceType(StrEnum):
    """Where the finetune draws its training pairs from.

    ``DIRECTORY`` scans a static document directory (``source_dir``);
    ``TRAJECTORY`` harvests the org's real working history (completed-task
    deliverables, EPISODIC distillation trajectories, and PROCEDURAL failure
    lessons) and curates by the golden-benchmark score.
    """

    DIRECTORY = "directory"
    TRAJECTORY = "trajectory"


class FineTuneRequest(BaseModel):
    """Request to start a fine-tuning pipeline run.

    Attributes:
        source_dir: Directory containing org documents for training.
        base_model: Base embedding model to fine-tune (``None`` = use
            current active model).
        output_dir: Directory to save checkpoints (``None`` = default).
        resume_run_id: Resume a previous failed/cancelled run.
        epochs: Override training epochs.
        learning_rate: Override learning rate.
        temperature: Override InfoNCE temperature.
        top_k: Override hard negative count.
        batch_size: Override training batch size.
        validation_split: Fraction of data held out for evaluation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data_source: FineTuneDataSourceType = Field(
        default=FineTuneDataSourceType.DIRECTORY,
        description="Where training pairs are drawn from",
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
        default=None,
        ge=1,
        description="Override training epochs",
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
        description="Fraction of data held out for evaluation",
    )

    @model_validator(mode="after")
    def _require_source_dir_in_directory_mode(self) -> Self:
        """Require ``source_dir`` when sourcing from a directory.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If directory mode is selected without a ``source_dir``.
        """
        if (
            self.data_source is FineTuneDataSourceType.DIRECTORY
            and self.source_dir is None
        ):
            msg = "source_dir is required when data_source is 'directory'"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _reject_path_traversal(self) -> Self:
        """Reject parent-directory traversal and Windows paths.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for field_name in ("source_dir", "output_dir"):
            val = getattr(self, field_name)
            if val is None:
                continue
            parts = PureWindowsPath(val).parts + PurePosixPath(val).parts
            if ".." in parts:
                msg = f"{field_name} must not contain parent-directory traversal (..)"
                raise ValueError(msg)
            if "\\" in val or (
                len(val) >= 2 and val[1] == ":"  # noqa: PLR2004
            ):
                msg = (
                    f"{field_name} must be a POSIX path (no backslashes "
                    "or drive letters)"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_base_model(self) -> Self:
        """Reject unsafe ``base_model`` references (RCE / SSRF defence).

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If ``base_model`` fails the safe-reference check.
        """
        _assert_safe_base_model(self.base_model)
        return self


_ACTIVE_STAGES: frozenset[FineTuneStage] = frozenset(
    {
        FineTuneStage.GENERATING_DATA,
        FineTuneStage.MINING_NEGATIVES,
        FineTuneStage.TRAINING,
        FineTuneStage.EVALUATING,
        FineTuneStage.DEPLOYING,
    }
)


class FineTuneStatus(BaseModel):
    """Status of the fine-tuning pipeline.

    Attributes:
        run_id: Current or most recent run ID (``None`` when idle
            with no history).
        stage: Current pipeline stage.
        progress: Progress fraction (0.0-1.0), ``None`` when idle.
        error: Error message if the pipeline failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    run_id: NotBlankStr | None = Field(
        default=None,
        description="Current or most recent run ID",
    )
    stage: FineTuneStage = Field(
        default=FineTuneStage.IDLE,
        description="Current pipeline stage",
    )
    progress: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Progress fraction (0.0-1.0)",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description="Error message if failed",
    )

    @model_validator(mode="after")
    def _validate_stage_invariants(self) -> Self:
        """Enforce valid (stage, progress, error) combinations.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.stage == FineTuneStage.IDLE:
            if self.progress is not None:
                msg = "progress must be None when stage is IDLE"
                raise ValueError(msg)
            if self.error is not None:
                msg = "error must be None when stage is IDLE"
                raise ValueError(msg)
        if self.stage == FineTuneStage.FAILED and self.error is None:
            msg = "error is required when stage is FAILED"
            raise ValueError(msg)
        if self.stage in _ACTIVE_STAGES and self.error is not None:
            msg = "error must be None during active pipeline stages"
            raise ValueError(msg)
        return self


# ── Evaluation metrics ───────────────────────────────────────────


class EvalMetrics(BaseModel):
    """Before/after evaluation metrics for a fine-tuned checkpoint.

    Attributes:
        ndcg_at_10: NDCG@10 of the fine-tuned model.
        recall_at_10: Recall@10 of the fine-tuned model.
        base_ndcg_at_10: NDCG@10 of the base model.
        base_recall_at_10: Recall@10 of the base model.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    ndcg_at_10: float = Field(ge=0.0, le=1.0, description="NDCG@10 fine-tuned")
    recall_at_10: float = Field(ge=0.0, le=1.0, description="Recall@10 fine-tuned")
    base_ndcg_at_10: float = Field(ge=0.0, le=1.0, description="NDCG@10 base")
    base_recall_at_10: float = Field(ge=0.0, le=1.0, description="Recall@10 base")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def improvement_ndcg(self) -> float:
        """Relative improvement in NDCG@10."""
        if self.base_ndcg_at_10 == 0.0:
            return 0.0
        return (self.ndcg_at_10 - self.base_ndcg_at_10) / self.base_ndcg_at_10

    @computed_field  # type: ignore[prop-decorator]
    @property
    def improvement_recall(self) -> float:
        """Relative improvement in Recall@10."""
        if self.base_recall_at_10 == 0.0:
            return 0.0
        return (self.recall_at_10 - self.base_recall_at_10) / self.base_recall_at_10


# ── Run configuration snapshot ──────────────────────────────────


class FineTuneRunConfig(BaseModel):
    """Frozen snapshot of the configuration used for a pipeline run.

    Attributes:
        source_dir: Source document directory.
        base_model: Base embedding model identifier.
        output_dir: Checkpoint output directory.
        epochs: Training epochs.
        learning_rate: Training learning rate.
        temperature: InfoNCE temperature.
        top_k: Hard negatives per query.
        batch_size: Training batch size.
        validation_split: Fraction held out for evaluation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data_source: FineTuneDataSourceType = Field(
        default=FineTuneDataSourceType.DIRECTORY,
        description="Where training pairs are drawn from",
    )
    source_dir: NotBlankStr | None = Field(
        default=None,
        description="Source document directory (directory mode only)",
    )
    base_model: NotBlankStr = Field(description="Base embedding model")
    output_dir: NotBlankStr = Field(description="Checkpoint output directory")
    epochs: int = Field(default=3, ge=1, description="Training epochs")
    learning_rate: float = Field(default=1e-5, gt=0.0, description="Learning rate")
    temperature: float = Field(default=0.02, gt=0.0, description="InfoNCE temperature")
    top_k: int = Field(default=4, ge=1, description="Hard negatives per query")
    batch_size: int = Field(default=128, ge=1, description="Training batch size")
    validation_split: float = Field(
        default=0.1,
        gt=0.0,
        lt=1.0,
        description="Fraction held out for evaluation",
    )

    @model_validator(mode="after")
    def _require_source_dir_in_directory_mode(self) -> Self:
        """Require ``source_dir`` when sourcing from a directory.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If directory mode is selected without a ``source_dir``.
        """
        if (
            self.data_source is FineTuneDataSourceType.DIRECTORY
            and self.source_dir is None
        ):
            msg = "source_dir is required when data_source is 'directory'"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_base_model(self) -> Self:
        """Reject unsafe ``base_model`` references (RCE / SSRF defence).

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If ``base_model`` fails the safe-reference check.
        """
        _assert_safe_base_model(self.base_model)
        return self


# ── Pipeline run record ─────────────────────────────────────────


class FineTuneRun(BaseModel):
    """Persistent record of a fine-tuning pipeline run.

    Attributes:
        id: Unique run identifier.
        stage: Current pipeline stage.
        progress: Progress fraction within current stage.
        error: Error message if the run failed.
        config: Frozen configuration snapshot.
        started_at: When the run was started.
        updated_at: Last status update timestamp.
        completed_at: When the run finished (completed/failed/cancelled).
        stages_completed: Stages that finished successfully.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4, description="Unique run identifier")
    stage: FineTuneStage = Field(description="Current pipeline stage")
    progress: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Progress within current stage",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description="Error message if failed",
    )
    config: FineTuneRunConfig = Field(description="Configuration snapshot")
    started_at: AwareDatetime = Field(description="Run start time")
    updated_at: AwareDatetime = Field(description="Last update time")
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="Run completion time",
    )
    stages_completed: tuple[str, ...] = Field(
        default=(),
        description="Successfully completed stage names",
    )

    @model_validator(mode="after")
    def _validate_run_invariants(self) -> Self:
        """Enforce stage/error/completed_at consistency.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        _terminal = frozenset(
            {FineTuneStage.COMPLETE, FineTuneStage.FAILED},
        )
        if self.stage == FineTuneStage.FAILED and self.error is None:
            msg = "error is required when stage is FAILED"
            raise ValueError(msg)
        if self.stage == FineTuneStage.COMPLETE and self.error is not None:
            msg = "error must be None when stage is COMPLETE"
            raise ValueError(msg)
        if self.stage in _ACTIVE_STAGES and self.error is not None:
            msg = "error must be None during active pipeline stages"
            raise ValueError(msg)
        if self.stage in _terminal and self.completed_at is None:
            msg = "completed_at is required for terminal stages"
            raise ValueError(msg)
        if self.stage not in _terminal and self.completed_at is not None:
            msg = "completed_at must be None for non-terminal stages"
            raise ValueError(msg)
        # Validate stage names in stages_completed.
        valid_names = frozenset(s.value for s in FineTuneStage)
        for name in self.stages_completed:
            if name not in valid_names:
                msg = f"Unknown stage name in stages_completed: {name!r}"
                raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_seconds(self) -> float | None:
        """Run duration in seconds (``None`` if not completed)."""
        if self.completed_at is None:
            return None
        delta = self.completed_at - self.started_at
        return delta.total_seconds()


# ── Checkpoint record ────────────────────────────────────────────


class CheckpointRecord(BaseModel):
    """Persistent record of a fine-tuned model checkpoint.

    Attributes:
        id: Unique checkpoint identifier.
        run_id: Pipeline run that produced this checkpoint.
        model_path: Path to the checkpoint directory.
        base_model: Base model identifier.
        doc_count: Number of training documents.
        eval_metrics: Evaluation metrics (``None`` if not evaluated).
        size_bytes: Checkpoint size on disk.
        created_at: When the checkpoint was saved.
        is_active: Whether this checkpoint is currently deployed.
        backup_config_json: JSON backup of pre-deployment config.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Unique checkpoint ID")
    run_id: NotBlankStr = Field(description="Originating run ID")
    model_path: NotBlankStr = Field(description="Path to checkpoint")
    base_model: NotBlankStr = Field(description="Base model identifier")
    doc_count: int = Field(ge=0, description="Training document count")
    eval_metrics: EvalMetrics | None = Field(
        default=None,
        description="Evaluation metrics",
    )
    size_bytes: int = Field(ge=0, description="Checkpoint size on disk")
    created_at: AwareDatetime = Field(description="Creation timestamp")
    is_active: bool = Field(default=False, description="Currently deployed")
    backup_config_json: str | None = Field(
        default=None,
        description="Pre-deployment config backup (JSON)",
    )


# ── Pre-flight validation ────────────────────────────────────────


class PreflightCheck(BaseModel):
    """Result of a single pre-flight validation check.

    Attributes:
        name: Check identifier.
        status: Pass/warn/fail result.
        message: Human-readable result description.
        detail: Optional additional detail.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Check identifier")
    status: Literal["pass", "warn", "fail"] = Field(description="Result")
    message: NotBlankStr = Field(description="Result description")
    detail: str | None = Field(default=None, description="Additional detail")


class PreflightResult(BaseModel):
    """Aggregated pre-flight validation results.

    Attributes:
        checks: Individual check results.
        recommended_batch_size: VRAM-based batch size recommendation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    checks: tuple[PreflightCheck, ...] = Field(
        default=(),
        description="Individual check results",
    )
    recommended_batch_size: int | None = Field(
        default=None,
        ge=1,
        description="VRAM-based batch size recommendation",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_proceed(self) -> bool:
        """True if no checks have ``"fail"`` status."""
        return all(c.status != "fail" for c in self.checks)


class FineTuneExecutionConfig(BaseModel):
    """Configuration for fine-tune pipeline execution backend.

    All numeric fields must be finite (no NaN or Inf).

    Attributes:
        backend: Execution backend -- ``"in-process"`` (default, lazy
            torch import) or ``"docker"`` (dedicated container).
        image: Container image for the ``"docker"`` backend.  Set by
            the CLI via ``SYNTHORG_FINE_TUNE_IMAGE`` in the backend
            container environment.  Required when ``backend="docker"``,
            ignored for ``"in-process"``.
        gpu_enabled: Request GPU passthrough on the container.  Only
            applies to ``backend="docker"``; ignored for in-process.
        memory_limit: Container memory limit (Docker format).
        timeout_seconds: Maximum wall-clock time for a single stage.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backend: Literal["in-process", "docker"] = "in-process"
    image: NotBlankStr | None = None
    gpu_enabled: bool = False
    memory_limit: NotBlankStr = "8g"
    timeout_seconds: float = Field(default=7200.0, gt=0)

    @model_validator(mode="after")
    def _validate_docker_requires_image(self) -> Self:
        """Ensure image is set when backend is docker.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.backend == "docker" and not self.image:
            msg = "image is required when backend='docker'"
            raise ValueError(msg)
        return self

"""MCP-facing fine-tune plan + backend-unsupported error.

The fine-tune runner consumes :class:`FineTuneRequest` (in
``memory.embedding.fine_tune_models``) which is tuned to the orchestrator
and runner protocols. MCP callers talk to :class:`MemoryService` via this
module's :class:`FineTunePlan` instead so the public contract stays
stable when the runner internals evolve.

``FineTunePlan.to_request`` builds a :class:`FineTuneRequest` on demand;
both models share the same path-traversal rejection so hostile
``source_dir`` / ``output_dir`` values fail validation before reaching
the filesystem.

:class:`BackendUnsupportedError` is raised by :class:`MemoryService`
fine-tune methods when the active persistence backend does not expose
``fine_tune_runs`` / ``fine_tune_checkpoints``. Its ``domain_code``
maps directly to ``"not_supported"`` so MCP handlers can route the
failure through :func:`synthorg.meta.mcp.handlers.common.not_supported`
without inspecting the exception body.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.domain_errors import DomainError
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic runtime
from synthorg.memory.embedding.fine_tune_models import (
    FineTuneExecutionConfig,
    FineTuneRequest,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Minimum length of a path with a Windows drive letter prefix ("C:").
# Declared before the class that uses it so the path-traversal check
# reads in source order.
_MIN_DRIVE_LETTER_LEN: Literal[2] = 2


class BackendUnsupportedError(DomainError):
    """Raised when the active persistence backend lacks fine-tune support.

    Carries ``domain_code = "not_supported"`` so handlers can map the
    failure onto the shared MCP envelope via either
    :func:`synthorg.meta.mcp.handlers.common.err` (which picks up
    ``exc.domain_code`` automatically) or
    :func:`synthorg.meta.mcp.handlers.common.not_supported` (which
    additionally emits the ``MCP_HANDLER_NOT_IMPLEMENTED`` WARNING
    event).

    Inherits :class:`DomainError` so the prefix-vs-category validator
    runs at class-definition time; the inherited
    ``ErrorCode.INTERNAL_ERROR`` default (8000, ``INTERNAL`` category)
    matches the prefix and emits a 500 by default through the API
    handler.  ``__slots__`` is kept on the local attribute set so
    ``domain_code`` stays a class constant.
    """

    __slots__ = ("reason",)

    domain_code: str = "not_supported"
    # Deterministic capability gate -- retrying cannot make the backend
    # suddenly grow fine-tune repos, so callers in the provider-retry
    # layer must surface this error immediately.
    is_retryable: bool = False

    def __init__(self, reason: str) -> None:
        """Initialize with the operator-visible reason string.

        Raises:
            ValueError: If *reason* is empty or whitespace-only.
        """
        if not reason or not reason.strip():
            msg = "BackendUnsupportedError.reason must be non-empty"
            raise ValueError(msg)
        self.reason = reason
        super().__init__(reason)


class ActiveEmbedderSnapshot(BaseModel):
    """Read-only snapshot of the currently active embedder.

    Attributes:
        provider: Active embedder provider identifier (e.g. ``"local"``).
        model: Active embedder model path or identifier.
        checkpoint_id: Active checkpoint id when a fine-tuned
            checkpoint is deployed; ``None`` when the base embedder is
            active.
        read_from_settings: ``True`` when provider/model were resolved
            via the settings service; ``False`` when no settings
            service is wired (values fall back to ``None``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    provider: NotBlankStr | None = Field(
        default=None,
        description="Active embedder provider identifier",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description="Active embedder model path or identifier",
    )
    checkpoint_id: NotBlankStr | None = Field(
        default=None,
        description="Active checkpoint id (None when base embedder is active)",
    )
    read_from_settings: bool = Field(
        description="Whether values were resolved via the settings service",
    )


class FineTunePlan(BaseModel):
    """MCP-facing plan used to start / resume fine-tune runs.

    Mirrors :class:`FineTuneRequest` field-for-field but isolates the
    public MCP contract from the runner's internal request type.
    ``to_request()`` builds the runner request on demand so handlers
    never hand the runner a partially-validated dict.

    Attributes:
        source_dir: Directory containing org documents for training.
        base_model: Base embedding model to fine-tune (``None`` = use
            current active model).
        output_dir: Directory to save checkpoints (``None`` = default).
        resume_run_id: Resume a previous failed/cancelled run.
        epochs: Override training epochs.
        learning_rate: Override learning rate.
        temperature: Override InfoNCE temperature.
        top_k: Override hard negative count per query.
        batch_size: Override training batch size.
        validation_split: Fraction held out for evaluation (exclusive
            bounds: 0 < v < 1).
        execution: Optional runner-backend configuration (in-process
            vs docker, gpu, memory, timeout).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    source_dir: NotBlankStr = Field(
        description="Directory containing org documents for training",
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
        description="Fraction held out for evaluation",
    )
    execution: FineTuneExecutionConfig | None = Field(
        default=None,
        description="Optional runner-backend execution config",
    )

    @model_validator(mode="after")
    def _reject_path_traversal(self) -> Self:
        """Reject parent-directory traversal, backslashes, drive letters.

        Applied to ``source_dir`` and ``output_dir``. Keeps the MCP
        surface from accepting Windows paths or ``..`` segments that
        the runner's subprocess / container mount would otherwise
        expose the host filesystem to.
        """
        for field_name in ("source_dir", "output_dir"):
            val = getattr(self, field_name)
            if val is None:
                continue
            parts = PureWindowsPath(val).parts + PurePosixPath(val).parts
            if ".." in parts:
                msg = f"{field_name} must not contain parent-directory traversal (..)"
                raise ValueError(msg)
            if "\\" in val or (len(val) >= _MIN_DRIVE_LETTER_LEN and val[1] == ":"):
                msg = (
                    f"{field_name} must be a POSIX path "
                    "(no backslashes or drive letters)"
                )
                raise ValueError(msg)
        return self

    def to_request(self) -> FineTuneRequest:
        """Build the internal runner request.

        The runner does not know about ``execution`` (it is routed via
        a separate path inside the orchestrator), so only the shared
        tuning fields are forwarded here. Copying field-by-field keeps
        validation contracts explicit; a generic ``model_dump``
        round-trip would silently fill any new fields on
        ``FineTuneRequest`` with ``None`` and break the runner's
        invariants.
        """
        return FineTuneRequest(
            source_dir=self.source_dir,
            base_model=self.base_model,
            output_dir=self.output_dir,
            resume_run_id=self.resume_run_id,
            epochs=self.epochs,
            learning_rate=self.learning_rate,
            temperature=self.temperature,
            top_k=self.top_k,
            batch_size=self.batch_size,
            validation_split=self.validation_split,
        )


__all__ = [
    "ActiveEmbedderSnapshot",
    "BackendUnsupportedError",
    "FineTunePlan",
]

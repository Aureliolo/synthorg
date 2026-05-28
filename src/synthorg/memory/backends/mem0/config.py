"""Mem0 backend configuration and config builder.

Isolates Mem0-specific settings from the core ``CompanyMemoryConfig``.
The ``build_mem0_config_dict`` function produces the dict that Mem0's
``Memory.from_config()`` expects.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_BACKEND_CONFIG_INVALID,
    MEMORY_EMBEDDER_CHECKPOINT_ACTIVE,
    MEMORY_EMBEDDER_CHECKPOINT_MISSING,
)

logger = get_logger(__name__)


class EmbeddingFineTuneConfig(BaseModel):
    """Optional domain-specific embedding fine-tuning configuration.

    When ``enabled`` is ``True`` and ``checkpoint_path`` is set, the
    Mem0 adapter uses the checkpoint path as the model identifier
    instead of the base ``Mem0EmbedderConfig.model``.  The embedding
    provider must serve the fine-tuned model under this identifier.

    Fine-tuning itself runs offline via the ``POST /admin/memory/fine-tune``
    endpoint (see ``MemoryAdminController``).  See
    ``docs/reference/embedding-evaluation.md`` for the full
    pipeline design.

    Attributes:
        enabled: Whether fine-tuning checkpoint lookup is active.
        checkpoint_path: Path to the fine-tuned model checkpoint.
            Required when ``enabled`` is ``True``.
        base_model: Identifier of the base model that was fine-tuned.
            Required when ``enabled`` is ``True``.
        training_data_dir: Directory containing training data for
            the offline fine-tuning pipeline.  Not required when
            ``enabled`` is ``True`` -- only consumed by the
            training step, not by checkpoint lookup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether fine-tuning checkpoint lookup is active",
    )
    checkpoint_path: NotBlankStr | None = Field(
        default=None,
        description="Path to the fine-tuned model checkpoint",
    )
    base_model: NotBlankStr | None = Field(
        default=None,
        description="Identifier of the base model that was fine-tuned",
    )
    training_data_dir: NotBlankStr | None = Field(
        default=None,
        description=("Directory containing training data for the fine-tuning pipeline"),
    )

    @model_validator(mode="after")
    def _validate_required_when_enabled(self) -> Self:
        """Require checkpoint_path and base_model when fine-tuning is enabled.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.enabled and self.checkpoint_path is None:
            msg = "checkpoint_path must be set when fine-tuning is enabled"
            logger.warning(
                MEMORY_BACKEND_CONFIG_INVALID,
                model="EmbeddingFineTuneConfig",
                field="checkpoint_path",
                enabled=self.enabled,
                reason=msg,
            )
            raise ValueError(msg)
        if self.enabled and self.base_model is None:
            msg = "base_model must be set when fine-tuning is enabled"
            logger.warning(
                MEMORY_BACKEND_CONFIG_INVALID,
                model="EmbeddingFineTuneConfig",
                field="base_model",
                enabled=self.enabled,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _reject_path_traversal(self) -> Self:
        """Reject parent-directory traversal and Windows paths.

        Consistent with ``Mem0BackendConfig._reject_traversal``.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for field_name in ("checkpoint_path", "training_data_dir"):
            val = getattr(self, field_name)
            if val is None:
                continue
            parts = PureWindowsPath(val).parts + PurePosixPath(val).parts
            if ".." in parts:
                msg = f"{field_name} must not contain parent-directory traversal (..)"
                logger.warning(
                    MEMORY_BACKEND_CONFIG_INVALID,
                    model="EmbeddingFineTuneConfig",
                    field=field_name,
                    value=val,
                    reason=msg,
                )
                raise ValueError(msg)
            if "\\" in val or (
                len(val) >= 2 and val[1] == ":"  # noqa: PLR2004
            ):
                msg = (
                    f"{field_name} must be a POSIX path (no backslashes "
                    "or drive letters) -- targets Linux containers"
                )
                logger.warning(
                    MEMORY_BACKEND_CONFIG_INVALID,
                    model="EmbeddingFineTuneConfig",
                    field=field_name,
                    value=val,
                    reason=msg,
                )
                raise ValueError(msg)
        return self


class Mem0EmbedderConfig(BaseModel):
    """Embedder settings for the Mem0 memory backend.

    Both ``provider`` and ``model`` are required -- callers must
    supply them explicitly so that vendor-specific identifiers stay
    out of source defaults.  The values must be valid Mem0 SDK
    identifiers (e.g. ``"example-provider"``,
    ``"example-medium-001"``); see the Mem0 documentation for
    supported providers and models.

    Attributes:
        provider: Embedding provider name (Mem0 SDK identifier).
        model: Embedding model identifier (Mem0 SDK identifier).
        dims: Embedding vector dimensions.
        fine_tune: Optional fine-tuning configuration (``None`` by
            default; when provided, disabled unless ``enabled`` is
            ``True``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(
        description="Embedding provider name (Mem0 SDK identifier)",
    )
    model: NotBlankStr = Field(
        description="Embedding model identifier (Mem0 SDK identifier)",
    )
    dims: int = Field(
        default=1536,
        ge=1,
        description="Embedding vector dimensions",
    )
    fine_tune: EmbeddingFineTuneConfig | None = Field(
        default=None,
        description="Optional fine-tuning configuration (None by default)",
    )


class EmbeddingCostConfig(BaseModel):
    """Configuration for embedding cost tracking.

    When ``enabled`` is ``True``, the Mem0 adapter records a
    ``CostRecord`` with ``call_category=EMBEDDING`` after each
    ``store()`` and ``retrieve()`` call.  Cost is estimated from
    text length using the ``default_chars_per_token`` heuristic
    and the per-model pricing in ``model_pricing``.

    .. note::

        Currently only settable programmatically via
        ``Mem0BackendConfig(embedding_cost=EmbeddingCostConfig(...))``.
        The YAML company config path does not yet surface this field.

    Attributes:
        enabled: Whether embedding cost tracking is active.
        model_pricing: Mapping of model name to cost per 1K input
            tokens (USD).  When the active model is not in this map,
            cost is recorded as 0.0 (free/local model).
        default_chars_per_token: Heuristic for converting character
            count to token count.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether embedding cost tracking is active",
    )
    model_pricing: dict[NotBlankStr, float] = Field(
        default_factory=dict,
        description=(
            "Model name to cost per 1K input tokens (USD). "
            "Missing models are treated as free."
        ),
    )
    default_chars_per_token: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Characters per token heuristic for estimation",
    )

    @model_validator(mode="after")
    def _validate_pricing_non_negative(self) -> Self:
        """Reject negative cost-per-1K values in model_pricing.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        for model_name, cost in self.model_pricing.items():
            if cost < 0.0:
                msg = (
                    f"model_pricing[{model_name!r}] = {cost} "
                    f"is negative -- cost must be >= 0.0"
                )
                logger.warning(
                    MEMORY_BACKEND_CONFIG_INVALID,
                    model="EmbeddingCostConfig",
                    field="model_pricing",
                    entry=model_name,
                    value=cost,
                    reason=msg,
                )
                raise ValueError(msg)
        return self


class Mem0BackendConfig(BaseModel):
    """Mem0-specific backend configuration.

    Attributes:
        data_dir: Directory for Mem0 data persistence.
        collection_name: Qdrant collection name.
        embedder: Embedder settings (required -- no defaults).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data_dir: NotBlankStr = Field(
        default="/data/memory",
        description="Directory for Mem0 data persistence",
    )
    collection_name: NotBlankStr = Field(
        default="synthorg_memories",
        description="Qdrant collection name",
    )
    embedder: Mem0EmbedderConfig = Field(
        description="Embedder settings",
    )
    sparse_search_enabled: bool = Field(
        default=False,
        description=(
            "Enable BM25 sparse vector search alongside dense retrieval. "
            "When True, a sparse vector field is added to the Qdrant "
            "collection and sparse vectors are upserted on store."
        ),
    )
    embedding_cost: EmbeddingCostConfig = Field(
        default_factory=EmbeddingCostConfig,
        description="Embedding cost tracking configuration",
    )

    @model_validator(mode="after")
    def _reject_traversal(self) -> Self:
        """Reject parent-directory traversal and Windows paths.

        The Mem0 backend targets Linux/Docker containers where paths
        must be POSIX.  Windows-style paths (drive letters, backslashes)
        are rejected to prevent accidental host-path leaks.

        Note: ``build_config_from_company_config`` passes ``data_dir``
        from ``CompanyMemoryConfig``, so this check also protects
        the factory path.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        parts = (
            PureWindowsPath(self.data_dir).parts + PurePosixPath(self.data_dir).parts
        )
        if ".." in parts:
            msg = "data_dir must not contain parent-directory traversal (..)"
            logger.warning(
                MEMORY_BACKEND_CONFIG_INVALID,
                backend="mem0",
                field="data_dir",
                value=self.data_dir,
                reason=msg,
            )
            raise ValueError(msg)
        if "\\" in self.data_dir or (
            len(self.data_dir) >= 2 and self.data_dir[1] == ":"  # noqa: PLR2004  # drive-letter check
        ):
            msg = (
                "data_dir must be a POSIX path (no backslashes or "
                "drive letters) -- the Mem0 backend targets Linux containers"
            )
            logger.warning(
                MEMORY_BACKEND_CONFIG_INVALID,
                backend="mem0",
                field="data_dir",
                value=self.data_dir,
                reason=msg,
            )
            raise ValueError(msg)
        return self


def _resolve_effective_model(embedder: Mem0EmbedderConfig) -> str:
    """Resolve the effective model identifier.

    When fine-tuning is enabled and a checkpoint path exists on disk,
    the checkpoint path is used as the model identifier.  If the
    checkpoint is missing, falls back to the base model with a
    warning.  Otherwise, the base model is returned.

    Args:
        embedder: Embedder configuration.

    Returns:
        The model identifier to pass to the Mem0 SDK.
    """
    ft = embedder.fine_tune
    if ft is not None and ft.enabled:
        if ft.checkpoint_path is None:  # pragma: no cover -- guaranteed by validator
            return ft.base_model or embedder.model
        checkpoint = Path(ft.checkpoint_path)
        if not checkpoint.exists():
            fallback = ft.base_model or embedder.model
            logger.warning(
                MEMORY_EMBEDDER_CHECKPOINT_MISSING,
                checkpoint_path=ft.checkpoint_path,
                base_model=fallback,
                reason="checkpoint not found on disk, falling back to base model",
            )
            return fallback
        logger.info(
            MEMORY_EMBEDDER_CHECKPOINT_ACTIVE,
            checkpoint_path=ft.checkpoint_path,
            base_model=ft.base_model,
        )
        return ft.checkpoint_path
    return embedder.model


def build_mem0_config_dict(config: Mem0BackendConfig) -> dict[str, Any]:
    """Build the dict that ``Memory.from_config()`` expects.

    When fine-tuning is enabled, the checkpoint path is used as the
    model identifier instead of the base model.

    Args:
        config: Mem0 backend configuration.

    Returns:
        Configuration dict suitable for ``Memory.from_config()``.
    """
    base_path = PurePosixPath(config.data_dir)
    effective_model = _resolve_effective_model(config.embedder)
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": config.collection_name,
                "embedding_model_dims": config.embedder.dims,
                "path": str(base_path / "qdrant"),
            },
        },
        "embedder": {
            "provider": config.embedder.provider,
            "config": {
                "model": effective_model,
            },
        },
        "history_db_path": str(base_path / "history.db"),
        # Mem0 config schema version -- required by Memory.from_config().
        "version": "v1.1",
    }


def build_config_from_company_config(
    config: CompanyMemoryConfig,
    *,
    embedder: Mem0EmbedderConfig,
    sparse_search_enabled: bool = False,
) -> Mem0BackendConfig:
    """Derive a ``Mem0BackendConfig`` from the top-level memory config.

    Args:
        config: Company-wide memory configuration.
        embedder: Embedder settings (provider and model must be
            supplied explicitly to avoid vendor names in defaults).
        sparse_search_enabled: Enable BM25 sparse vector search
            alongside dense retrieval.

    Returns:
        Mem0-specific backend configuration.

    Raises:
        ValueError: If the storage config specifies a vector or
            history store that the Mem0 backend does not support,
            or if ``data_dir`` contains parent-directory traversal
            (``..``) -- propagated from ``Mem0BackendConfig``
            validation.
    """
    if config.storage.vector_store != "qdrant":
        msg = (
            f"Mem0 backend only supports embedded qdrant vector store, "
            f"got {config.storage.vector_store!r}"
        )
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            field="vector_store",
            value=config.storage.vector_store,
            reason=msg,
        )
        raise ValueError(msg)
    if config.storage.history_store != "sqlite":
        msg = (
            f"Mem0 backend only supports sqlite history store, "
            f"got {config.storage.history_store!r}"
        )
        logger.warning(
            MEMORY_BACKEND_CONFIG_INVALID,
            backend="mem0",
            field="history_store",
            value=config.storage.history_store,
            reason=msg,
        )
        raise ValueError(msg)
    return Mem0BackendConfig(
        data_dir=config.storage.data_dir,
        embedder=embedder,
        sparse_search_enabled=sparse_search_enabled,
    )

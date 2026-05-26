"""Memory configuration models.

Frozen Pydantic models for company-wide memory backend selection
and backend-specific settings.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import (
    ConsolidationInterval,
    MemoryLevel,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.backends.composite.config import (
    CompositeBackendConfig,  # noqa: TC001
)
from synthorg.memory.consolidation.config import ConsolidationConfig
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors

logger = get_logger(__name__)


class MemoryStorageConfig(BaseModel):
    """Storage-specific memory configuration.

    Attributes:
        data_dir: Directory path for memory data persistence.
        vector_store: Vector store backend name.
        history_store: History store backend name.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _VALID_VECTOR_STORES: ClassVar[frozenset[str]] = frozenset(
        {"qdrant", "qdrant-external"},
    )
    _VALID_HISTORY_STORES: ClassVar[frozenset[str]] = frozenset(
        {"sqlite", "postgresql"},
    )

    data_dir: NotBlankStr = Field(
        default="/data/memory",
        description=(
            "Directory path for memory data persistence.  "
            "Default targets a Docker volume mount -- override "
            "for local development."
        ),
    )
    vector_store: NotBlankStr = Field(
        default="qdrant",
        description="Vector store backend name",
    )
    history_store: NotBlankStr = Field(
        default="sqlite",
        description="History store backend name",
    )

    @model_validator(mode="after")
    def _validate_store_names(self) -> Self:
        """Ensure vector_store and history_store are recognized values.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.vector_store not in self._VALID_VECTOR_STORES:
            msg = (
                f"Unknown vector_store {self.vector_store!r}. "
                f"Valid stores: {sorted(self._VALID_VECTOR_STORES)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="vector_store",
                value=self.vector_store,
                reason=msg,
            )
            raise ValueError(msg)
        if self.history_store not in self._VALID_HISTORY_STORES:
            msg = (
                f"Unknown history_store {self.history_store!r}. "
                f"Valid stores: {sorted(self._VALID_HISTORY_STORES)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="history_store",
                value=self.history_store,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _reject_traversal(self) -> Self:
        """Reject parent-directory traversal to prevent path escapes.

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
                CONFIG_VALIDATION_FAILED,
                field="data_dir",
                value=self.data_dir,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class MemoryOptionsConfig(BaseModel):
    """Memory behaviour options.

    Attributes:
        retention_days: Days to retain memories (``None`` = forever).
        max_memories_per_agent: Maximum memories per agent.
        consolidation_interval: How often to consolidate memories.
        shared_knowledge_base: Whether shared knowledge is enabled.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Days to retain memories (None = forever)",
    )
    max_memories_per_agent: int = Field(
        default=10_000,
        ge=1,
        description="Maximum memories per agent",
    )
    consolidation_interval: ConsolidationInterval = Field(
        default=ConsolidationInterval.DAILY,
        description="How often to consolidate memories",
    )
    shared_knowledge_base: bool = Field(
        default=True,
        description="Whether shared knowledge is enabled",
    )


class EmbedderOverrideConfig(BaseModel):
    """User-facing embedder override configuration.

    Allows users to override the auto-selected embedding model via
    company YAML config, runtime settings, or template config.  All
    fields are optional -- ``None`` means "use auto-selection".

    Attributes:
        provider: Embedding provider name override.
        model: Embedding model identifier override.
        dims: Embedding vector dimensions (required when ``model``
            is set, since dimensions are model-dependent).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider: NotBlankStr | None = Field(
        default=None,
        description="Embedding provider name override",
    )
    model: NotBlankStr | None = Field(
        default=None,
        description="Embedding model identifier override",
    )
    dims: int | None = Field(
        default=None,
        ge=1,
        description="Embedding vector dimensions",
    )

    @model_validator(mode="after")
    def _model_requires_dims(self) -> Self:
        """Require dims when model is set, and model when dims is set.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.model is not None and self.dims is None:
            msg = (
                "dims must be set when model is overridden "
                "(dimensions are model-dependent)"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="dims",
                reason=msg,
            )
            raise ValueError(msg)
        if self.dims is not None and self.model is None:
            msg = (
                "model must be set when dims is overridden "
                "(dimensions are model-dependent)"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="model",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class CompanyMemoryConfig(BaseModel):
    """Top-level company-wide memory configuration.

    Attributes:
        backend: Memory backend name (validated against ``_VALID_BACKENDS``).
        level: Default memory persistence level.
        storage: Storage-specific settings.
        options: Memory behaviour options.
        retrieval: Memory retrieval pipeline settings.
        consolidation: Memory consolidation settings.
        embedder: Optional embedder override (``None`` = auto-select).
        procedural: Procedural memory auto-generation settings.
        composite: Composite backend routing config (required when
            backend is ``"composite"``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset(
        {"mem0", "composite", "inmemory"},
    )

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="backend",
            namespace=SettingNamespace.MEMORY,
            key="backend",
        ),
        MirrorField(
            field="level",
            namespace=SettingNamespace.MEMORY,
            key="default_level",
        ),
    )

    backend: NotBlankStr = Field(
        default="mem0",
        description="Memory backend name",
    )
    level: MemoryLevel = Field(
        default=MemoryLevel.SESSION,
        description="Default memory persistence level",
    )
    storage: MemoryStorageConfig = Field(
        default_factory=MemoryStorageConfig,
        description="Storage-specific settings",
    )
    options: MemoryOptionsConfig = Field(
        default_factory=MemoryOptionsConfig,
        description="Memory behaviour options",
    )
    retrieval: MemoryRetrievalConfig = Field(
        default_factory=MemoryRetrievalConfig,
        description="Memory retrieval pipeline settings",
    )
    consolidation: ConsolidationConfig = Field(
        default_factory=ConsolidationConfig,
        description="Memory consolidation settings",
    )
    embedder: EmbedderOverrideConfig | None = Field(
        default=None,
        description=(
            "Optional embedder override.  When set, overrides "
            "auto-selection for provider, model, and/or dims."
        ),
    )
    procedural: ProceduralMemoryConfig = Field(
        default_factory=ProceduralMemoryConfig,
        description=(
            "Procedural memory auto-generation settings.  Controls "
            "whether failure-driven skill proposals are generated, "
            "which model to use, and quality thresholds."
        ),
    )
    composite: CompositeBackendConfig | None = Field(
        default=None,
        description=(
            "Composite backend routing configuration.  "
            "Required when backend is ``'composite'``."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        """Apply mirrors.

        Returns:
            Result of type ``Any``.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

    @model_validator(mode="after")
    def _validate_backend_name(self) -> Self:
        """Ensure backend is a known memory backend.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.backend not in self._VALID_BACKENDS:
            msg = (
                f"Unknown memory backend {self.backend!r}. "
                f"Valid backends: {sorted(self._VALID_BACKENDS)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="backend",
                value=self.backend,
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_composite_config(self) -> Self:
        """Require composite config when backend is ``"composite"``.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.backend == "composite" and self.composite is None:
            msg = "composite config is required when backend is 'composite'"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="composite",
                reason=msg,
            )
            raise ValueError(msg)
        if self.backend != "composite" and self.composite is not None:
            msg = "composite config is only valid when backend is 'composite'"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                field="composite",
                reason=msg,
            )
            raise ValueError(msg)
        return self

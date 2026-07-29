"""Memory configuration models.

Frozen Pydantic models for company-wide memory backend selection
and backend-specific settings.
"""

from pathlib import PurePosixPath, PureWindowsPath
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.composite.config import (
    CompositeBackendConfig,
)
from synthorg.memory.consolidation.config import ConsolidationConfig
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.self_editing_models import SelfEditingMemoryConfig
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors

logger = get_logger(__name__)


class MemoryStorageConfig(BaseModel):
    """Storage-specific memory configuration.

    Vectors and their lexical index live in the operational database, so
    the only filesystem path memory still needs is the one backends use
    for artefacts kept outside it.

    Attributes:
        data_dir: Directory path for memory data persistence.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    data_dir: NotBlankStr = Field(
        default="/data/memory",
        description=(
            "Directory path for memory data persistence.  "
            "Default targets a Docker volume mount -- override "
            "for local development."
        ),
    )

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

    The consolidation cadence lives on ``ConsolidationConfig.interval``,
    which is the field the scheduler reads and the
    ``memory.consolidation_interval`` setting mirrors. A second copy
    here would be a knob an operator could turn with no effect.

    Attributes:
        retention_days: Days to retain memories (``None`` = forever).
        max_memories_per_agent: Maximum memories per agent.
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
    shared_knowledge_base: bool = Field(
        default=True,
        description="Whether shared knowledge is enabled",
    )


class EmbedderOverrideConfig(BaseModel):
    """One layer of an operator's embedder override.

    Set from company YAML config, runtime settings, or template config.
    Every field is independently optional because this is a patch over the
    layer below, not the whole answer: pinning a width here while the model
    comes from another layer is a supported combination, so the completeness
    rules (a model needs its provider, a width needs a model) are enforced
    once on the merged result in ``resolve_embedder_config`` rather than
    here, where no layer can see what the others supplied.

    Attributes:
        provider: Embedding provider name override.
        model: Embedding model identifier override.
        dims: Embedding vector width override, pinning the width instead of
            measuring it from the model.
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


class CompanyMemoryConfig(BaseModel):
    """Top-level company-wide memory configuration.

    Attributes:
        backend: Memory backend name (validated against ``_VALID_BACKENDS``).
        storage: Storage-specific settings.
        options: Memory behaviour options.
        retrieval: Memory retrieval pipeline settings.
        consolidation: Memory consolidation settings.
        embedder: Optional embedder override (``None`` = no override at
            this layer; runtime settings decide, and resolution refuses if
            no layer ever names a model).
        procedural: Procedural memory auto-generation settings.
        composite: Composite backend routing config (required when
            backend is ``"composite"``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset(
        {"sqlvector", "composite", "inmemory"},
    )

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="backend",
            namespace=SettingNamespace.MEMORY,
            key="backend",
            only_if_env_set=True,
        ),
    )

    backend: NotBlankStr = Field(
        default="sqlvector",
        description="Memory backend name",
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
            "Optional embedder binding from company YAML. Runtime settings "
            "override it per field; resolution refuses unless some layer "
            "names both a provider and a model."
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
    self_editing: SelfEditingMemoryConfig = Field(
        default_factory=SelfEditingMemoryConfig,
        description=(
            "MemGPT-style self-editing memory settings, used when "
            "``retrieval.strategy`` is ``self_editing``: which categories "
            "the agent may write, whether core writes are allowed, and the "
            "per-tier entry caps."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Apply mirrors.

        Returns:
            The input data with any unset mirror fields populated.
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

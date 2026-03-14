"""Org memory configuration models.

Frozen Pydantic models for organizational memory backend selection
and behaviour settings.
"""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.org.access_control import WriteAccessConfig
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class ExtendedStoreConfig(BaseModel):
    """Configuration for the extended org facts store.

    Attributes:
        backend: Store backend name (e.g. ``"sqlite"``).
        max_retrieved_per_query: Maximum facts to retrieve per query.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset({"sqlite"})

    backend: NotBlankStr = Field(
        default="sqlite",
        description="Store backend name",
    )
    max_retrieved_per_query: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Maximum facts to retrieve per query",
    )

    @model_validator(mode="after")
    def _validate_backend_name(self) -> Self:
        """Ensure backend is a known store backend."""
        if self.backend not in self._VALID_BACKENDS:
            msg = (
                f"Unknown extended store backend {self.backend!r}. "
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


class OrgMemoryConfig(BaseModel):
    """Top-level organizational memory configuration.

    Attributes:
        backend: Org memory backend name.
        core_policies: Core policy texts injected into system prompts.
        extended_store: Extended facts store configuration.
        write_access: Write access control configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset(
        {"hybrid_prompt_retrieval"},
    )

    backend: NotBlankStr = Field(
        default="hybrid_prompt_retrieval",
        description="Org memory backend name",
    )
    core_policies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Core policy texts injected into system prompts",
    )
    extended_store: ExtendedStoreConfig = Field(
        default_factory=ExtendedStoreConfig,
        description="Extended facts store configuration",
    )
    write_access: WriteAccessConfig = Field(
        default_factory=WriteAccessConfig,
        description="Write access control configuration",
    )

    @model_validator(mode="after")
    def _validate_backend_name(self) -> Self:
        """Ensure backend is a known org memory backend."""
        if self.backend not in self._VALID_BACKENDS:
            msg = (
                f"Unknown org memory backend {self.backend!r}. "
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

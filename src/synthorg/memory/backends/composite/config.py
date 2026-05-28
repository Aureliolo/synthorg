"""Configuration for the composite memory backend."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class CompositeBackendConfig(BaseModel):
    """Namespace-to-backend routing configuration.

    Maps storage namespaces (e.g. ``"memories"``, ``"scratch"``)
    to named backend implementations.  Namespaces not listed fall
    back to ``default``.

    Attributes:
        routes: Mapping from namespace to backend name.
        default: Backend name for unmapped namespaces.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    routes: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Mapping from namespace to backend name",
    )
    default: NotBlankStr = Field(
        default="inmemory",
        description="Backend name for unmapped namespaces",
    )

"""Configuration for the composite memory backend."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class CompositeBackendConfig(BaseModel):
    """Namespace-to-backend routing configuration.

    Maps storage namespaces (e.g. ``"memories"``, ``"scratch"``)
    to named backend implementations.  Namespaces not listed fall
    back to ``default``.

    The runtime type of ``routes`` is :class:`types.MappingProxyType`,
    expressed in the annotation as :class:`collections.abc.Mapping` so
    callers see an immutable interface at the type boundary instead of a
    freely mutable ``dict`` that ``frozen=True`` would leave mutable.

    Attributes:
        routes: Mapping from namespace to backend name (read-only).
        default: Backend name for unmapped namespaces.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    routes: Mapping[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Mapping from namespace to backend name",
    )
    default: NotBlankStr = Field(
        default="inmemory",
        description="Backend name for unmapped namespaces",
    )

    @model_validator(mode="after")
    def _wrap_routes_readonly(self) -> Self:
        """Wrap the routes mapping in a MappingProxyType for immutability.

        Returns:
            The validated config.
        """
        object.__setattr__(self, "routes", MappingProxyType(dict(self.routes)))
        return self

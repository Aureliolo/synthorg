"""Configuration for the composite memory backend."""

import copy
from collections.abc import Iterator, Mapping
from typing import Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class _FrozenRoutes(Mapping[NotBlankStr, NotBlankStr]):
    """Read-only, deepcopy-friendly namespace-to-backend mapping.

    ``MappingProxyType`` gives the read-only view but cannot be
    deepcopied (it has no ``__reduce__``), so a frozen config carrying
    one raises ``TypeError`` from ``model_copy(deep=True)``. Wrapping a
    private dict keeps the immutable interface while staying copyable;
    :class:`collections.abc.Mapping` supplies the dict-style ``__eq__``
    so ``routes == {...}`` still holds.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[NotBlankStr, NotBlankStr]) -> None:
        self._data: dict[NotBlankStr, NotBlankStr] = dict(data)

    @override
    def __getitem__(self, key: NotBlankStr) -> NotBlankStr:
        return self._data[key]

    @override
    def __iter__(self) -> Iterator[NotBlankStr]:
        return iter(self._data)

    @override
    def __len__(self) -> int:
        return len(self._data)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._data!r})"

    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenRoutes:
        return _FrozenRoutes(copy.deepcopy(self._data, memo))


class CompositeBackendConfig(BaseModel):
    """Namespace-to-backend routing configuration.

    Maps storage namespaces (e.g. ``"memories"``, ``"scratch"``)
    to named backend implementations.  Namespaces not listed fall
    back to ``default``.

    The runtime type of ``routes`` is :class:`_FrozenRoutes`, expressed
    in the annotation as :class:`collections.abc.Mapping` so callers see
    an immutable interface at the type boundary instead of a freely
    mutable ``dict`` that ``frozen=True`` would leave mutable.

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
        """Wrap the routes mapping in a read-only view for immutability.

        Returns:
            The validated config.
        """
        object.__setattr__(self, "routes", _FrozenRoutes(self.routes))
        return self

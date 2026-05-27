"""Feature-manifest substrate.

Every feature directory ships a thin ``feature.py`` exposing a module-level
``FEATURE`` that satisfies :class:`FeatureModule`. The manifest declares the
feature's whole surface: its settings namespace, typed state slice, Litestar
controllers, MCP contributions, lifecycle hooks, boot-constructed ("ghost-wired")
symbols, and the other features it depends on.

The substrate exposes three things the rest of the package composes against:

- :class:`BaseFeatureStateSlice`: the frozen base every per-feature state slice
  subclasses. ``api.state.AppState`` holds one slice per feature and hands it to
  controllers via a typed lookup.
- :class:`FeatureModule` (Protocol) + :class:`FeatureManifest` (the concrete
  frozen model features instantiate).
- :func:`discover_features`: a filesystem walk that imports every ``feature.py``
  under :mod:`synthorg` and returns the manifests in dependency order.

Discovery walks the filesystem for files named ``feature.py`` and imports only
those (rather than importing every package to traverse, which would pull in
optional extras); a malformed or unimportable ``feature.py`` fails loudly.
"""

import heapq
import importlib
from collections.abc import (
    Iterable,  # noqa: TC003 -- runtime use in resolve_feature_order signature (typeguard-ready)
)
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from litestar import (
    Controller,  # noqa: TC002 -- Pydantic resolves the type[Controller] field at runtime
)
from pydantic import BaseModel, ConfigDict

import synthorg
from synthorg.core.domain_errors import DomainError, ServiceUnavailableError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.settings.enums import (
    SettingNamespace,  # noqa: TC001 -- Pydantic resolves this field type at runtime
)

__all__ = [
    "BaseFeatureStateSlice",
    "FeatureDependencyError",
    "FeatureManifest",
    "FeatureModule",
    "LifecycleHook",
    "McpHandlerDescriptor",
    "McpHandlerModule",
    "discover_features",
    "feature_directories",
    "require_service",
    "resolve_feature_order",
]


class FeatureDependencyError(DomainError):
    """Raised when the feature dependency graph cannot be resolved.

    Covers an unknown dependency, a duplicate feature name, a dependency
    cycle, or a ``feature.py`` that does not expose a valid manifest. This
    is a boot-time configuration failure, hence the internal category.
    """

    default_message: ClassVar[str] = "Feature dependency graph is invalid"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_DEPENDENCY_ERROR
    status_code: ClassVar[int] = 500


def require_service[ServiceT](value: ServiceT | None, label: str) -> ServiceT:
    """Return *value* or raise 503 when a feature slice field is unwired.

    The slice-reader counterpart to the historic ``AppState._require_service``
    seam: controllers and MCP handlers read a slice field (typed ``T | None``)
    and pass it through this guard so a not-yet-wired service surfaces as a
    clean ``ServiceUnavailableError`` rather than an ``AttributeError``.

    Args:
        value: The slice field value (``None`` when the service is unwired).
        label: Human-readable service name for the 503 message.

    Returns:
        The non-``None`` service.

    Raises:
        ServiceUnavailableError: When *value* is ``None``.
    """
    if value is None:
        msg = f"{label} not configured"
        raise ServiceUnavailableError(msg)
    return value


class BaseFeatureStateSlice(BaseModel):
    """Frozen base for a feature's slice of application state.

    Service references are typed ``T | None`` on subclasses: ``None`` means
    not-yet-wired (the controller raises 503), mirroring the historic
    ``has_<service>`` guard. The slice is frozen, so a hot-reload composes a
    new slice and swaps it atomically rather than mutating in place.

    ``arbitrary_types_allowed`` lets subclasses hold plain (non-Pydantic)
    service references; Pydantic merges it down the MRO, so each subclass
    only redeclares the ``frozen`` / ``extra`` keys the immutability gate
    requires per class.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


@runtime_checkable
class LifecycleHook(Protocol):
    """A named async startup/shutdown hook a feature contributes.

    Declarative this PR: ``api.app`` keeps appending its own hooks. The field
    feeds the navigation index and is consumed by the Part-3 composition root.

    Members are read-only so frozen concrete descriptors satisfy the Protocol.
    """

    @property
    def name(self) -> str: ...

    async def __call__(self) -> None: ...


@runtime_checkable
class McpHandlerModule(Protocol):
    """A feature's MCP contribution descriptor.

    Minimal structural surface the navigation index reads. Concrete
    descriptors may carry the full tool definitions and handler mapping for
    the Part-3 composition root; the substrate only constrains what it uses.

    Members are read-only so frozen concrete descriptors satisfy the Protocol.
    """

    @property
    def domain(self) -> str: ...

    @property
    def tool_names(self) -> tuple[str, ...]: ...


@runtime_checkable
class FeatureModule(Protocol):
    """The declarative surface of a single feature.

    A feature directory's ``feature.py`` exposes ``FEATURE: FeatureModule``.
    :class:`FeatureManifest` is the concrete implementation features build.
    Members are read-only so the frozen :class:`FeatureManifest` satisfies the
    Protocol (a frozen model's fields are not assignable, so writable Protocol
    members would not match).
    """

    @property
    def name(self) -> str: ...

    @property
    def settings_namespace(self) -> SettingNamespace | None: ...

    @property
    def state_slice(self) -> type[BaseFeatureStateSlice] | None: ...

    @property
    def controllers(self) -> tuple[type[Controller], ...]: ...

    @property
    def mcp_handlers(self) -> tuple[McpHandlerModule, ...]: ...

    @property
    def lifecycle_hooks(self) -> tuple[LifecycleHook, ...]: ...

    @property
    def ghost_wired_symbols(self) -> tuple[str, ...]: ...

    @property
    def depends_on(self) -> tuple[str, ...]: ...


class McpHandlerDescriptor(BaseModel):
    """Concrete, frozen :class:`McpHandlerModule` a feature declares.

    Names the MCP domain and the fully-qualified tool names the feature
    contributes; the navigation index reads these. The actual tool definitions
    and handler mapping continue to live in ``meta/mcp/{domains,handlers}``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    domain: str
    tool_names: tuple[str, ...]


class FeatureManifest(BaseModel):
    """Concrete, frozen feature manifest satisfying :class:`FeatureModule`.

    ``arbitrary_types_allowed`` lets the model carry the controller classes,
    slice class, and structural descriptors directly; the model stays frozen
    and rejects unknown fields so a typo in a ``feature.py`` fails at import.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: str
    settings_namespace: SettingNamespace | None = None
    state_slice: type[BaseFeatureStateSlice] | None = None
    controllers: tuple[type[Controller], ...] = ()
    mcp_handlers: tuple[McpHandlerModule, ...] = ()
    lifecycle_hooks: tuple[LifecycleHook, ...] = ()
    ghost_wired_symbols: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


_FEATURE_FILE = "feature.py"
_FEATURE_ATTR = "FEATURE"

_discovery_cache: tuple[FeatureModule, ...] | None = None


def resolve_feature_order(
    manifests: Iterable[FeatureModule],
) -> tuple[FeatureModule, ...]:
    """Return the manifests in dependency order (dependencies first).

    Independent features are ordered deterministically by name, so the result
    is stable across runs regardless of discovery order.

    Args:
        manifests: The discovered feature manifests, in any order.

    Returns:
        The manifests sorted so every feature appears after all features it
        lists in ``depends_on``.

    Raises:
        FeatureDependencyError: On a duplicate feature name, a dependency on an
            unknown feature, or a dependency cycle.
    """
    items = tuple(manifests)
    by_name: dict[str, FeatureModule] = {}
    for manifest in items:
        if manifest.name in by_name:
            msg = f"duplicate feature name: {manifest.name!r}"
            raise FeatureDependencyError(msg)
        by_name[manifest.name] = manifest

    in_degree: dict[str, int] = dict.fromkeys(by_name, 0)
    successors: dict[str, list[str]] = {name: [] for name in by_name}
    for manifest in items:
        for dependency in manifest.depends_on:
            if dependency not in by_name:
                msg = (
                    f"feature {manifest.name!r} depends on unknown "
                    f"feature {dependency!r}"
                )
                raise FeatureDependencyError(msg)
            successors[dependency].append(manifest.name)
            in_degree[manifest.name] += 1

    ready = [name for name, degree in in_degree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        name = heapq.heappop(ready)
        ordered.append(name)
        for successor in sorted(successors[name]):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                heapq.heappush(ready, successor)

    if len(ordered) != len(by_name):
        unresolved = sorted(set(by_name) - set(ordered))
        msg = f"feature dependency graph contains a cycle among {unresolved}"
        raise FeatureDependencyError(msg)

    return tuple(by_name[name] for name in ordered)


def _iter_feature_module_names() -> list[str]:
    """Return the dotted module names of every ``feature.py`` under synthorg.

    Returns:
        Dotted module names (e.g. ``synthorg.meta.charter.feature``), sorted.
    """
    package_root = Path(synthorg.__file__).parent
    source_root = package_root.parent
    names: list[str] = []
    for path in sorted(package_root.rglob(_FEATURE_FILE)):
        relative = path.relative_to(source_root).with_suffix("")
        names.append(".".join(relative.parts))
    return names


def _load_manifests() -> tuple[FeatureModule, ...]:
    """Import every ``feature.py`` and collect its ``FEATURE`` manifest.

    Returns:
        The discovered manifests in filesystem order (unsorted by dependency).

    Raises:
        FeatureDependencyError: When a ``feature.py`` does not expose a
            ``FEATURE`` attribute satisfying :class:`FeatureModule`.
    """
    manifests: list[FeatureModule] = []
    for module_name in _iter_feature_module_names():
        module = importlib.import_module(module_name)
        feature = getattr(module, _FEATURE_ATTR, None)
        if not isinstance(feature, FeatureModule):
            msg = (
                f"{module_name} does not expose a valid "
                f"{_FEATURE_ATTR}: FeatureModule manifest"
            )
            raise FeatureDependencyError(msg)
        manifests.append(feature)
    return tuple(manifests)


def feature_directories() -> dict[str, str]:
    """Map each feature name to its repository-relative package directory.

    The directory is the parent of the feature's ``feature.py`` (e.g.
    ``src/synthorg/meta/charter``). Used by the navigation-index generator
    and the manifest gate, which need a feature's location, not just its
    manifest.

    Returns:
        Mapping of feature name to repo-relative directory (posix).
    """
    directories: dict[str, str] = {}
    for module_name in _iter_feature_module_names():
        module = importlib.import_module(module_name)
        feature = getattr(module, _FEATURE_ATTR, None)
        if isinstance(feature, FeatureModule):
            package_parts = module_name.split(".")[:-1]
            directories[feature.name] = "src/" + "/".join(package_parts)
    return directories


def discover_features(*, force: bool = False) -> tuple[FeatureModule, ...]:
    """Discover and dependency-order every feature manifest.

    Walks the filesystem for ``feature.py`` modules, imports each, validates
    that it exposes a :class:`FeatureModule`, and returns them in dependency
    order. The result is memoised; pass ``force=True`` to rebuild (used by the
    index generator and tests).

    Args:
        force: Rebuild the cache instead of returning the memoised result.

    Returns:
        The dependency-ordered feature manifests.

    Raises:
        FeatureDependencyError: When a ``feature.py`` is malformed or the
            dependency graph cannot be resolved.
    """
    global _discovery_cache  # noqa: PLW0603 -- single boot-time memoisation seam
    if _discovery_cache is None or force:
        _discovery_cache = resolve_feature_order(_load_manifests())
    return _discovery_cache

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
    Callable,
    Iterable,
    Mapping,
)
from pathlib import Path
from typing import ClassVar, Literal, Protocol, runtime_checkable

from litestar import (
    Controller,
)
from litestar.handlers import WebsocketRouteHandler
from pydantic import BaseModel, ConfigDict

import synthorg
from synthorg.core.domain_errors import DomainError, ServiceUnavailableError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import (
    NotBlankStr,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.enums import (
    SettingNamespace,
)

__all__ = [
    "BaseFeatureStateSlice",
    "ControllerRegistration",
    "FeatureDependencyError",
    "FeatureManifest",
    "FeatureModule",
    "McpHandlerDescriptor",
    "McpHandlerModule",
    "ServiceLifecycleHook",
    "discover_features",
    "feature_directories",
    "require_service",
    "resolve_feature_order",
]

logger = get_logger(__name__)


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
class ServiceLifecycleHook(Protocol):
    """A feature's named start/stop hook, ordered by feature dependency.

    The composition root collects every feature's hooks, starts them in
    dependency order, and stops the ones that started in reverse order. The
    timeout + fatal metadata lets the dispatcher reproduce the historic
    ``_safe_startup`` / ``_safe_shutdown`` per-service budgets and
    fail-fast-vs-best-effort distinctions:

    - ``start_timeout_seconds`` / ``stop_timeout_seconds``: per-phase budget
      (``None`` means no explicit budget). A stop that exceeds its budget is
      logged and abandoned, never allowed to wedge shutdown.
    - ``fatal_on_start_error``: when ``True`` a failed ``start`` aborts boot
      (after rolling back already-started hooks); when ``False`` the failure
      is logged best-effort and boot continues.

    A pure wiring hook with nothing to tear down implements ``stop`` as a
    no-op. Members are read-only so frozen concrete descriptors satisfy the
    Protocol.
    """

    @property
    def name(self) -> str: ...

    @property
    def start_timeout_seconds(self) -> float | None: ...

    @property
    def stop_timeout_seconds(self) -> float | None: ...

    @property
    def fatal_on_start_error(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


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
    def controllers(self) -> tuple[type[Controller] | ControllerRegistration, ...]: ...

    @property
    def websocket_handlers(self) -> tuple[WebsocketRouteHandler, ...]: ...

    @property
    def mcp_handlers(self) -> tuple[McpHandlerModule, ...]: ...

    @property
    def lifecycle_hooks(self) -> tuple[ServiceLifecycleHook, ...]: ...

    @property
    def ghost_wired_symbols(self) -> tuple[str, ...]: ...

    @property
    def depends_on(self) -> tuple[str, ...]: ...


class ControllerRegistration(BaseModel):
    """A controller plus the metadata the composition root mounts it with.

    Lets a feature register a controller conditionally and choose its mount
    point. A bare ``type[Controller]`` in a manifest's ``controllers`` tuple
    is equivalent to ``ControllerRegistration(controller=cls)`` (always
    mounted, api-prefixed).

    - ``predicate``: when set, the controller mounts only if the predicate
      returns ``True`` against the live ``AppState`` at route-assembly time.
      ``None`` mounts unconditionally. This preserves the historic
      404-when-unwired behaviour for integration / optional controllers (an
      unmounted route 404s rather than 503-ing every dashboard poll). The
      predicate is typed against ``object`` to keep the substrate free of an
      ``api.state.AppState`` import cycle; callers pass the ``AppState``.
    - ``mount``: ``"api"`` mounts under the API prefix (the default);
      ``"root"`` mounts at the application root (e.g. a2a ``/.well-known``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    controller: type[Controller]
    predicate: Callable[[object], bool] | None = None
    mount: Literal["api", "root"] = "api"


class McpHandlerDescriptor(BaseModel):
    """Concrete, frozen :class:`McpHandlerModule` a feature declares.

    Names the MCP domain and tool names the navigation index reads, and
    carries the feature's tool definitions + handler map so the composition
    root can build the MCP registry and dispatch table from discovery rather
    than a hand-maintained central list.

    - ``tool_defs``: the feature's ``MCPToolDef`` instances (typed ``object``
      here so the low-level substrate does not import the ``meta.mcp`` domain;
      the registry builder casts them back).
    - ``handlers_factory``: a deferred factory returning the feature's
      ``{tool_name: ToolHandler}`` map. Deferred (a callable, not an eager
      map) so importing a ``feature.py`` during discovery does not pull the
      handler graph at import time. Values are ``ToolHandler`` instances,
      typed ``object`` here for the same layering reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    domain: str
    tool_names: tuple[str, ...]
    tool_defs: tuple[object, ...] = ()
    handlers_factory: Callable[[], Mapping[str, object]] | None = None


class FeatureManifest(BaseModel):
    """Concrete, frozen feature manifest satisfying :class:`FeatureModule`.

    ``arbitrary_types_allowed`` lets the model carry the controller classes,
    slice class, and structural descriptors directly; the model stays frozen
    and rejects unknown fields so a typo in a ``feature.py`` fails at import.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    name: NotBlankStr
    settings_namespace: SettingNamespace | None = None
    state_slice: type[BaseFeatureStateSlice] | None = None
    controllers: tuple[type[Controller] | ControllerRegistration, ...] = ()
    websocket_handlers: tuple[WebsocketRouteHandler, ...] = ()
    mcp_handlers: tuple[McpHandlerModule, ...] = ()
    lifecycle_hooks: tuple[ServiceLifecycleHook, ...] = ()
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
            logger.error(
                API_APP_STARTUP,
                action="feature_dependency_invalid",
                reason="duplicate_name",
                feature=manifest.name,
            )
            msg = f"duplicate feature name: {manifest.name!r}"
            raise FeatureDependencyError(msg)
        by_name[manifest.name] = manifest

    in_degree: dict[str, int] = dict.fromkeys(by_name, 0)
    successors: dict[str, list[str]] = {name: [] for name in by_name}
    for manifest in items:
        for dependency in manifest.depends_on:
            if dependency not in by_name:
                logger.error(
                    API_APP_STARTUP,
                    action="feature_dependency_invalid",
                    reason="unknown_dependency",
                    feature=manifest.name,
                    dependency=dependency,
                )
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
        logger.error(
            API_APP_STARTUP,
            action="feature_dependency_invalid",
            reason="cycle",
            features=unresolved,
        )
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


def _require_feature_manifest(module_name: str) -> FeatureModule:
    """Import *module_name* and return its validated ``FEATURE`` manifest.

    Args:
        module_name: Dotted module name of a ``feature.py``.

    Returns:
        The module-level ``FEATURE`` manifest.

    Raises:
        FeatureDependencyError: When the module does not expose a valid
            ``FEATURE: FeatureModule`` manifest.
    """
    module = importlib.import_module(module_name)
    feature = getattr(module, _FEATURE_ATTR, None)
    if not isinstance(feature, FeatureModule):
        logger.error(
            API_APP_STARTUP,
            action="feature_manifest_invalid",
            module=module_name,
        )
        msg = (
            f"{module_name} does not expose a valid "
            f"{_FEATURE_ATTR}: FeatureModule manifest"
        )
        raise FeatureDependencyError(msg)
    return feature


def _load_manifests() -> tuple[FeatureModule, ...]:
    """Import every ``feature.py`` and collect its ``FEATURE`` manifest.

    Returns:
        The discovered manifests in filesystem order (unsorted by dependency).

    Raises:
        FeatureDependencyError: When a ``feature.py`` does not expose a
            ``FEATURE`` attribute satisfying :class:`FeatureModule`.
    """
    return tuple(
        _require_feature_manifest(module_name)
        for module_name in _iter_feature_module_names()
    )


def feature_directories() -> dict[str, str]:
    """Map each feature name to its repository-relative package directory.

    The directory is the parent of the feature's ``feature.py`` (e.g.
    ``src/synthorg/meta/charter``). Used by the navigation-index generator
    and the manifest gate, which need a feature's location, not just its
    manifest.

    Returns:
        Mapping of feature name to repo-relative directory (posix).

    Raises:
        FeatureDependencyError: When a ``feature.py`` does not expose a valid
            manifest (matching :func:`_load_manifests`, so the two never
            disagree on which features exist).
    """
    directories: dict[str, str] = {}
    for module_name in _iter_feature_module_names():
        feature = _require_feature_manifest(module_name)
        if feature.name in directories:
            logger.error(
                API_APP_STARTUP,
                action="feature_dependency_invalid",
                reason="duplicate_name",
                feature=feature.name,
            )
            msg = f"duplicate feature name: {feature.name!r}"
            raise FeatureDependencyError(msg)
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
        logger.info(
            API_APP_STARTUP,
            action="features_discovered",
            count=len(_discovery_cache),
        )
    return _discovery_cache

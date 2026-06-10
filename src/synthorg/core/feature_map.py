"""AI-navigation feature index domain model.

A :class:`FeatureIndex` is a navigable, deterministic catalogue of every
internal SynthOrg feature: one :class:`FeatureMap` per discovered feature,
each declaring the feature's directory, settings namespace, exported
Protocols, REST controllers, MCP tool names, ghost-wired symbols, and the
fields its typed state slice declares. The index is built once per
generator run from the feature-manifest substrate
(:mod:`synthorg._core.features`), persisted to ``data/feature_index.json``,
and queried by AI agents through the ``synthorg_meta_query_feature_map`` MCP
tool so an agent reads ONE document to learn the whole feature surface.

The peer :class:`~synthorg.core.codebase_structure_map.CodebaseStructureMap`
models EXTERNAL brownfield-imported codebases; :class:`FeatureIndex` models
the INTERNAL SynthOrg surface. Different concerns, sibling files.
"""

import importlib
from pathlib import Path
from typing import Self

from litestar import Controller
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg._core.features import ControllerRegistration, FeatureModule
from synthorg.core.codebase_structure_map import RelPath
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.registry import REGISTRY_FEATURE_IMPORT_FAILED
from synthorg.observability.redaction import safe_error_description

logger = get_logger(__name__)

FEATURE_INDEX_SCHEMA_VERSION: int = 1


class FeatureMap(BaseModel):
    """One feature's navigable surface.

    Frozen, JSON-round-trippable value object. ``settings_namespace`` is
    ``None`` for features without an operator-facing namespace (the
    feature still has a typed state slice; the absence simply records
    that no ``settings/definitions/<name>.py`` exists).

    Attributes:
        name: Feature name (matches ``FeatureManifest.name``).
        directory: Repository-relative feature directory (e.g.
            ``src/synthorg/meta/charter``).
        settings_namespace: The ``SettingNamespace`` value (string form),
            or ``None`` when the feature has no operator namespace.
        protocol_exports: Public ``@runtime_checkable`` Protocol names the
            feature's top-level package exports for cross-feature use.
        controllers: REST controller class names the feature registers.
        mcp_tool_names: MCP tool names the feature contributes (across all
            of its ``McpHandlerDescriptor`` entries, flattened).
        ghost_wired_symbols: Boot-constructed class / factory names the
            feature owns; the ghost-wiring gate enforces parity with
            ``scripts/_ghost_wiring_manifest.txt``.
        state_slice_fields: Field names declared on the feature's typed
            state slice.
        depends_on: Other feature names this feature depends on (boot
            order resolved by ``resolve_feature_order``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Feature name")
    directory: RelPath = Field(description="Repository-relative feature directory")
    settings_namespace: NotBlankStr | None = Field(
        default=None, description="SettingNamespace value (string) or None"
    )
    protocol_exports: tuple[NotBlankStr, ...] = Field(
        default=(), description="Public Protocol class names exported by the feature"
    )
    controllers: tuple[NotBlankStr, ...] = Field(
        default=(), description="REST controller class names the feature registers"
    )
    mcp_tool_names: tuple[NotBlankStr, ...] = Field(
        default=(), description="MCP tool names the feature contributes"
    )
    ghost_wired_symbols: tuple[NotBlankStr, ...] = Field(
        default=(), description="Boot-constructed class / factory names owned"
    )
    state_slice_fields: tuple[NotBlankStr, ...] = Field(
        default=(), description="Field names declared on the feature's state slice"
    )
    depends_on: tuple[NotBlankStr, ...] = Field(
        default=(), description="Other feature names this feature depends on"
    )


class FeatureIndex(BaseModel):
    """The full AI-navigation index over every discovered feature.

    Attributes:
        schema_version: Version of the index schema; bumped on any
            field-shape change so consumers can detect drift.
        generated_at: Timestamp the index was built (UTC).
        features: One :class:`FeatureMap` per discovered feature, sorted
            deterministically by name so two regenerations of the same
            tree produce identical JSON.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    schema_version: int = Field(description="Index schema version")
    generated_at: AwareDatetime = Field(description="Index build timestamp (UTC)")
    features: tuple[FeatureMap, ...] = Field(
        default=(), description="Per-feature navigation maps, sorted by name"
    )

    @model_validator(mode="after")
    def _reject_duplicate_feature_names(self) -> Self:
        """Reject an index containing two features with the same name.

        Returns:
            ``Self`` instance after validation.

        Raises:
            ValueError: When ``features`` contains a duplicate
                ``FeatureMap.name``.
        """
        seen: set[str] = set()
        for feature in self.features:
            if feature.name in seen:
                msg = f"duplicate feature name in index: {feature.name!r}"
                raise ValueError(msg)
            seen.add(feature.name)
        return self


def protocol_exports(directory: str) -> tuple[str, ...]:
    """Return the ``@runtime_checkable`` Protocol names a feature package exports.

    Imports the package and walks its ``__all__``, returning only names whose
    referent is a runtime-checkable Protocol (detected via the private
    ``_is_protocol`` attribute typing adds to Protocol subclasses).

    Args:
        directory: Repository-relative feature directory (e.g.
            ``src/synthorg/meta/charter``). The leading ``src/`` is stripped
            and the remainder converted to a dotted package name.

    Returns:
        Tuple of exported Protocol class names. Empty when the package fails
        to import (best-effort: missing optional deps would otherwise block
        the whole index build).
    """
    package_name = ".".join(Path(directory).parts[1:])
    try:
        package = importlib.import_module(package_name)
    except (ImportError, AttributeError) as exc:
        # Best-effort covers only import-shape failures (missing
        # optional deps, absent submodule attribute); any other error
        # is a real defect in the feature package and must propagate.
        # Log so a silently-dropped feature package is visible in the
        # index-build output rather than vanishing from the catalogue.
        logger.warning(
            REGISTRY_FEATURE_IMPORT_FAILED,
            package=package_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return ()
    exported: tuple[str, ...] = tuple(getattr(package, "__all__", ()))
    return tuple(
        name
        for name in exported
        if getattr(getattr(package, name, None), "_is_protocol", False)
    )


def _controller_class(
    entry: type[Controller] | ControllerRegistration,
) -> type[Controller]:
    """Return the controller class from a manifest ``controllers`` entry.

    A manifest may list a bare ``type[Controller]`` or a
    :class:`ControllerRegistration` wrapping one with mount / predicate
    metadata; both resolve to the underlying class.

    Args:
        entry: A bare controller class or a registration wrapping one.

    Returns:
        The underlying controller class.
    """
    if isinstance(entry, ControllerRegistration):
        return entry.controller
    return entry


def build_feature_map(feature: FeatureModule, directory: str) -> FeatureMap:
    """Assemble a :class:`FeatureMap` from one discovered manifest.

    Single source of truth used by both ``scripts/generate_feature_index.py``
    (writes ``data/feature_index.json``) and the
    ``synthorg_meta_query_feature_map`` MCP handler (returns the same shape
    in-memory). Keeping the assembly here guarantees the persisted artefact
    and the live MCP response cannot drift.

    Args:
        feature: A discovered :class:`FeatureModule` (typically a frozen
            :class:`~synthorg._core.features.FeatureManifest`).
        directory: The repo-relative directory the feature lives in (from
            :func:`synthorg._core.features.feature_directories`).

    Returns:
        Frozen :class:`FeatureMap` ready to drop into a :class:`FeatureIndex`.
    """
    namespace = feature.settings_namespace
    mcp_tool_names = tuple(
        name for handler in feature.mcp_handlers for name in handler.tool_names
    )
    slice_type = feature.state_slice
    state_slice_fields = (
        tuple(slice_type.model_fields) if slice_type is not None else ()
    )
    return FeatureMap(
        name=feature.name,
        directory=directory,
        settings_namespace=namespace.value if namespace is not None else None,
        protocol_exports=protocol_exports(directory),
        controllers=tuple(
            _controller_class(entry).__name__ for entry in feature.controllers
        ),
        mcp_tool_names=mcp_tool_names,
        ghost_wired_symbols=feature.ghost_wired_symbols,
        state_slice_fields=state_slice_fields,
        depends_on=feature.depends_on,
    )

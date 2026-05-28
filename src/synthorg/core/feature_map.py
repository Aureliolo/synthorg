"""AI-navigation feature index domain model.

A :class:`FeatureIndex` is a navigable, deterministic catalogue of every
internal SynthOrg feature: one :class:`FeatureMap` per discovered feature,
each declaring the feature's directory, settings namespace, exported
Protocols, REST controllers, MCP tool names, ghost-wired symbols, and the
fields its typed state slice declares. The index is built once per
generator run from the feature-manifest substrate
(:mod:`synthorg._core.features`), persisted to ``data/feature_index.json``,
and queried by AI agents through the ``synthorg_query_feature_map`` MCP
tool so an agent reads ONE document to learn the whole feature surface.

The peer :class:`~synthorg.core.codebase_structure_map.CodebaseStructureMap`
models EXTERNAL brownfield-imported codebases; :class:`FeatureIndex` models
the INTERNAL SynthOrg surface. Different concerns, sibling files.
"""

from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.codebase_structure_map import RelPath  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


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

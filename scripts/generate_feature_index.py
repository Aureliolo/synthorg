#!/usr/bin/env python3
"""Generate the AI-navigation index from the feature-manifest substrate.

Writes two artefacts under ``data/``:

- ``feature_index.json``: a :class:`~synthorg.core.feature_map.FeatureIndex`
  (one :class:`~synthorg.core.feature_map.FeatureMap` per discovered feature)
  so an agent reads one file to learn what owns a feature, what it exports,
  and where to extend it.
- ``codebase_map.json``: per module under ``src/synthorg/``, its module-kind
  tier, tier LOC cap, current LOC, and owning feature (longest-directory-
  prefix match).

Run from the repo root::

    uv run python scripts/generate_feature_index.py
"""

import importlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _module_size_lib import (
    TIER_LIMITS,
    count_loc,
    resolve_tier,
)

from synthorg._core.features import (
    FeatureModule,
    discover_features,
    feature_directories,
)
from synthorg.core.feature_map import (
    FeatureIndex,
    FeatureMap,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_ROOT = _REPO_ROOT / "src" / "synthorg"
_DATA_DIR = _REPO_ROOT / "data"
_SCHEMA_VERSION = 1


def _package_name(directory: str) -> str:
    """Convert a repo-relative ``src/...`` directory to a dotted package name."""
    return ".".join(Path(directory).parts[1:])


def _protocol_exports(directory: str) -> tuple[str, ...]:
    """Return the runtime-checkable Protocol names a feature package exports."""
    try:
        package = importlib.import_module(_package_name(directory))
    except ImportError:
        return ()
    exported: tuple[str, ...] = tuple(getattr(package, "__all__", ()))
    return tuple(
        name
        for name in exported
        if getattr(getattr(package, name, None), "_is_protocol", False)
    )


def _state_slice_fields(feature: FeatureModule) -> tuple[str, ...]:
    """Return the field names declared on the feature's state slice."""
    slice_type = feature.state_slice
    if slice_type is None:
        return ()
    return tuple(slice_type.model_fields)


def _build_feature_map(feature: FeatureModule, directory: str) -> FeatureMap:
    """Assemble a :class:`FeatureMap` from a discovered manifest."""
    namespace = feature.settings_namespace
    mcp_tool_names = tuple(
        name for handler in feature.mcp_handlers for name in handler.tool_names
    )
    return FeatureMap(
        name=feature.name,
        directory=directory,
        settings_namespace=namespace.value if namespace is not None else None,
        protocol_exports=_protocol_exports(directory),
        controllers=tuple(controller.__name__ for controller in feature.controllers),
        mcp_tool_names=mcp_tool_names,
        ghost_wired_symbols=feature.ghost_wired_symbols,
        state_slice_fields=_state_slice_fields(feature),
        depends_on=feature.depends_on,
    )


def build_feature_index() -> FeatureIndex:
    """Build the in-memory feature index from the discovered manifests.

    Sorts the per-feature maps by name so two regenerations of the same
    tree produce identical JSON regardless of the dependency-resolver's
    boot ordering.

    Returns:
        Frozen, JSON-round-trippable :class:`FeatureIndex`.
    """
    directories = feature_directories()
    features_by_name = sorted(discover_features(force=True), key=lambda f: f.name)
    maps = tuple(
        _build_feature_map(feature, directories.get(feature.name, ""))
        for feature in features_by_name
    )
    return FeatureIndex(
        schema_version=_SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        features=maps,
    )


def _owning_feature(module_rel: str, directories: dict[str, str]) -> str | None:
    """Return the feature whose directory is the longest prefix of *module_rel*."""
    best_name: str | None = None
    best_len = -1
    for name, directory in directories.items():
        prefix = directory.removeprefix("src/")
        if module_rel.startswith(prefix + "/") and len(prefix) > best_len:
            best_name, best_len = name, len(prefix)
    return best_name


def build_codebase_map() -> list[dict[str, object]]:
    """Build the per-module codebase map (kind, tier cap, LOC, owning feature)."""
    directories = feature_directories()
    entries: list[dict[str, object]] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(_SRC_ROOT.parent.parent).as_posix()
        module_rel = path.relative_to(_SRC_ROOT.parent).as_posix()
        tier = resolve_tier(path, project_root=_REPO_ROOT)
        entries.append(
            {
                "module": rel,
                "kind": tier,
                "loc_cap": TIER_LIMITS.get(tier),
                "loc": count_loc(path),
                "owning_feature": _owning_feature(module_rel, directories),
            }
        )
    return entries


def _atomic_write_json(target: Path, payload: object) -> None:
    """Write *payload* as pretty JSON to *target* atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, delete=False
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(target)


def _warm_import_graph() -> None:
    """Import the application graph in its canonical order.

    Walking ``feature.py`` modules cold (before ``synthorg`` is otherwise
    imported) would trip the latent ``core.agent`` import cycle that the boot
    path resolves by ordering. Importing the app factory first establishes
    that order so the per-feature walk imports against a warm graph.
    """
    import synthorg.api.app  # noqa: F401


def main() -> int:
    """Generate both navigation-index artefacts. Returns 0 on success."""
    _warm_import_graph()
    index = build_feature_index()
    _atomic_write_json(_DATA_DIR / "feature_index.json", index.model_dump(mode="json"))
    _atomic_write_json(
        _DATA_DIR / "codebase_map.json", {"modules": build_codebase_map()}
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

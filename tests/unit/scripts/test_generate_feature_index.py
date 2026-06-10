"""Tests for the feature-index generator.

The generator walks ``discover_features()`` to build a :class:`FeatureIndex`
and the per-module ``codebase_map.json`` so an AI agent reads one document
to understand the whole feature surface.
"""

import importlib.util
from pathlib import Path
from typing import Protocol, cast

import pytest

from synthorg.core.feature_map import FeatureIndex
from tests._shared import JsonDict

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "generate_feature_index.py"


class _GeneratorModule(Protocol):
    """Subset of ``scripts/generate_feature_index.py``."""

    @staticmethod
    def build_feature_index() -> FeatureIndex: ...
    @staticmethod
    def build_codebase_map() -> list[JsonDict]: ...


def _load() -> _GeneratorModule:
    spec = importlib.util.spec_from_file_location("generate_feature_index", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_GeneratorModule, module)


_GENERATOR = _load()


def test_build_feature_index_contains_all_discovered_features() -> None:
    """Every feature in the live tree must appear in the index."""
    index = _GENERATOR.build_feature_index()
    names = {feature.name for feature in index.features}
    assert "charter" in names
    assert "engine" in names
    assert "api_core" in names
    assert len(index.features) >= 30


def test_build_feature_index_resolves_charter_surface() -> None:
    """Charter's feature map exposes its full surface."""
    index = _GENERATOR.build_feature_index()
    charter = next(f for f in index.features if f.name == "charter")
    assert charter.directory == "src/synthorg/meta/charter"
    assert charter.settings_namespace == "charter"
    assert "CharterController" in charter.controllers
    assert "CharterInterviewService" in charter.ghost_wired_symbols
    assert "CharterDispatcher" in charter.ghost_wired_symbols
    assert "interview_service" in charter.state_slice_fields
    assert "dispatcher" in charter.state_slice_fields
    assert any(name.startswith("synthorg_charter_") for name in charter.mcp_tool_names)


def test_build_feature_index_features_sorted_by_name() -> None:
    """Index ordering is deterministic across regenerations."""
    index = _GENERATOR.build_feature_index()
    names = [feature.name for feature in index.features]
    assert names == sorted(names)


def test_build_codebase_map_includes_per_module_metadata() -> None:
    """Every Python file under src/synthorg/ appears in the codebase map."""
    entries = _GENERATOR.build_codebase_map()
    assert entries, "codebase map must not be empty"
    sample = entries[0]
    assert {"module", "kind", "loc", "owning_feature"} <= set(sample.keys())
    modules = {entry["module"] for entry in entries}
    assert "src/synthorg/api/state.py" in modules


def test_build_codebase_map_assigns_owning_feature() -> None:
    """A file under a feature dir gets that feature as owner."""
    entries = _GENERATOR.build_codebase_map()
    by_module = {entry["module"]: entry for entry in entries}
    charter_entry = by_module["src/synthorg/meta/charter/state.py"]
    assert charter_entry["owning_feature"] == "charter"


def test_index_round_trips_through_json() -> None:
    """The generated index round-trips through ``FeatureIndex`` validation."""
    index = _GENERATOR.build_feature_index()
    restored = FeatureIndex.model_validate_json(index.model_dump_json())
    assert restored == index

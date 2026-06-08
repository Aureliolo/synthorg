"""Tests for workflow blueprint loader."""

from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.engine.workflow import blueprint_loader as _bl_module
from synthorg.engine.workflow.blueprint_errors import (
    BlueprintNotFoundError,
    BlueprintValidationError,
)
from synthorg.engine.workflow.blueprint_loader import (
    BUILTIN_BLUEPRINTS,
    BlueprintInfo,
    list_blueprints,
    list_builtin_blueprints,
    load_blueprint,
)
from synthorg.engine.workflow.enums import WorkflowNodeType


@pytest.fixture(autouse=True)
def _isolate_user_blueprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent user-override blueprints from leaking into tests."""
    monkeypatch.setattr(_bl_module, "_USER_BLUEPRINTS_DIR", tmp_path)


# Minimal valid YAML for user-override testing.
_USER_YAML = """\
blueprint:
  name: "custom-bp"
  display_name: "Custom Blueprint"
  description: "A user-defined blueprint"
  workflow_type: "sequential_pipeline"
  tags:
    - "custom"
  nodes:
    - id: "start"
      type: "start"
      label: "Start"
    - id: "task-1"
      type: "task"
      label: "Do work"
      config:
        title: "Work"
    - id: "end"
      type: "end"
      label: "End"
  edges:
    - id: "e1"
      source_node_id: "start"
      target_node_id: "task-1"
      type: "sequential"
    - id: "e2"
      source_node_id: "task-1"
      target_node_id: "end"
      type: "sequential"
"""


# ── list_builtin_blueprints ──────────────────────────────────────


class TestListBuiltinBlueprints:
    """Verify built-in blueprint registry."""

    @pytest.mark.unit
    def test_returns_sorted_names(self) -> None:
        names = list_builtin_blueprints()
        assert names == tuple(sorted(names))

    @pytest.mark.unit
    def test_contains_all_registered(self) -> None:
        names = list_builtin_blueprints()
        for name in BUILTIN_BLUEPRINTS:
            assert name in names

    @pytest.mark.unit
    def test_count_matches_registry(self) -> None:
        assert len(list_builtin_blueprints()) == len(BUILTIN_BLUEPRINTS)


# ── load_blueprint ───────────────────────────────────────────────


class TestLoadBlueprint:
    """Verify loading individual blueprints."""

    @pytest.mark.unit
    @pytest.mark.parametrize("name", sorted(BUILTIN_BLUEPRINTS))
    def test_loads_each_builtin(self, name: str) -> None:
        bp = load_blueprint(name)
        assert bp.name == name
        assert len(bp.nodes) >= 3  # start + at least 1 task + end
        assert len(bp.edges) >= 2

    @pytest.mark.unit
    def test_not_found_raises(self) -> None:
        with pytest.raises(BlueprintNotFoundError, match="Unknown"):
            load_blueprint("nonexistent-blueprint")

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "invalid_name",
        ["../etc/passwd", "foo/bar", ".hidden", "UPPER", ""],
    )
    def test_invalid_name_rejected(self, invalid_name: str) -> None:
        with pytest.raises(BlueprintNotFoundError):
            load_blueprint(invalid_name)

    @pytest.mark.unit
    def test_case_normalized(self) -> None:
        """Loading with uppercase letters works via normalization."""
        bp = load_blueprint("Feature-Pipeline")
        assert bp.name == "feature-pipeline"

    @pytest.mark.unit
    def test_user_override(self, tmp_path: Path) -> None:
        """User directory blueprints take precedence."""
        user_file = tmp_path / "feature-pipeline.yaml"
        user_file.write_text(_USER_YAML.replace("custom-bp", "feature-pipeline"))

        with patch(
            "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
            tmp_path,
        ):
            bp = load_blueprint("feature-pipeline")
            # Should load from user dir (has "Custom Blueprint" display name
            # after we patch the name).
            assert bp.display_name == "Custom Blueprint"

    @pytest.mark.unit
    def test_user_blueprint_not_in_builtins(self, tmp_path: Path) -> None:
        """User blueprints with novel names are loadable."""
        user_file = tmp_path / "custom-bp.yaml"
        user_file.write_text(_USER_YAML)

        with patch(
            "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
            tmp_path,
        ):
            bp = load_blueprint("custom-bp")
            assert bp.name == "custom-bp"
            assert bp.display_name == "Custom Blueprint"


# ── list_blueprints ──────────────────────────────────────────────


class TestListBlueprints:
    """Verify the full blueprint listing."""

    @pytest.mark.unit
    def test_returns_blueprint_info_instances(self) -> None:
        infos = list_blueprints()
        for info in infos:
            assert isinstance(info, BlueprintInfo)

    @pytest.mark.unit
    def test_returns_sorted_by_name(self) -> None:
        infos = list_blueprints()
        names = [i.name for i in infos]
        assert names == sorted(names)

    @pytest.mark.unit
    def test_all_builtins_present(self) -> None:
        infos = list_blueprints()
        names = {i.name for i in infos}
        for builtin_name in BUILTIN_BLUEPRINTS:
            assert builtin_name in names

    @pytest.mark.unit
    def test_info_has_counts(self) -> None:
        infos = list_blueprints()
        for info in infos:
            assert info.node_count >= 3
            assert info.edge_count >= 2

    @pytest.mark.unit
    def test_user_overrides_builtin(self, tmp_path: Path) -> None:
        """User blueprints shadow built-in ones."""
        user_file = tmp_path / "feature-pipeline.yaml"
        user_file.write_text(_USER_YAML.replace("custom-bp", "feature-pipeline"))

        with patch(
            "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
            tmp_path,
        ):
            infos = list_blueprints()
            fp_info = next(i for i in infos if i.name == "feature-pipeline")
            assert fp_info.source == "user"
            assert fp_info.display_name == "Custom Blueprint"


# ── Builtin YAML validity ────────────────────────────────────────


class TestBuiltinBlueprintsValidity:
    """Validate all built-in blueprint YAMLs structurally."""

    @pytest.mark.unit
    @pytest.mark.parametrize("name", sorted(BUILTIN_BLUEPRINTS))
    def test_valid_schema(self, name: str) -> None:
        bp = load_blueprint(name)
        assert bp.name
        assert bp.display_name
        assert bp.description

    @pytest.mark.unit
    @pytest.mark.parametrize("name", sorted(BUILTIN_BLUEPRINTS))
    def test_has_start_and_end(self, name: str) -> None:
        bp = load_blueprint(name)
        types = [n.type for n in bp.nodes]
        assert WorkflowNodeType.START in types
        assert WorkflowNodeType.END in types

    @pytest.mark.unit
    @pytest.mark.parametrize("name", sorted(BUILTIN_BLUEPRINTS))
    def test_edges_reference_existing_nodes(self, name: str) -> None:
        bp = load_blueprint(name)
        node_ids = {n.id for n in bp.nodes}
        for edge in bp.edges:
            assert edge.source_node_id in node_ids
            assert edge.target_node_id in node_ids

    @pytest.mark.unit
    @pytest.mark.parametrize("name", sorted(BUILTIN_BLUEPRINTS))
    def test_has_tags(self, name: str) -> None:
        bp = load_blueprint(name)
        assert len(bp.tags) > 0


# ── Error handling ───────────────────────────────────────────────


class TestBlueprintErrorHandling:
    """Error paths in blueprint loading."""

    @pytest.mark.unit
    def test_invalid_yaml_raises_validation_error(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad-bp.yaml"
        bad_file.write_text("key: [unclosed bracket")

        with (
            patch(
                "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
                tmp_path,
            ),
            pytest.raises(BlueprintValidationError, match="parse"),
        ):
            load_blueprint("bad-bp")

    @pytest.mark.unit
    def test_missing_blueprint_key_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "no-key.yaml"
        bad_file.write_text("something_else:\n  name: test\n")

        with (
            patch(
                "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
                tmp_path,
            ),
            pytest.raises(BlueprintValidationError, match="top-level"),
        ):
            load_blueprint("no-key")

    @pytest.mark.unit
    def test_schema_violation_raises(self, tmp_path: Path) -> None:
        """Blueprint missing required start/end nodes."""
        bad_yaml = """\
blueprint:
  name: "broken"
  display_name: "Broken"
  nodes:
    - id: "task-1"
      type: "task"
      label: "Only task"
  edges: []
"""
        bad_file = tmp_path / "broken.yaml"
        bad_file.write_text(bad_yaml)

        with (
            patch(
                "synthorg.engine.workflow.blueprint_loader._USER_BLUEPRINTS_DIR",
                tmp_path,
            ),
            pytest.raises(BlueprintValidationError, match="validation"),
        ):
            load_blueprint("broken")

"""Tests for the template pack loader."""

from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.templates.errors import TemplateNotFoundError
from synthorg.templates.pack_loader import (
    BUILTIN_PACKS,
    PackInfo,
    list_builtin_packs,
    list_packs,
    load_pack,
)


@pytest.mark.unit
class TestListBuiltinPacks:
    """Tests for list_builtin_packs()."""

    def test_returns_sorted_names(self) -> None:
        result = list_builtin_packs()
        assert result == tuple(sorted(result))

    def test_contains_all_registered(self) -> None:
        result = list_builtin_packs()
        assert set(result) == set(BUILTIN_PACKS)

    def test_count_matches_registry(self) -> None:
        assert len(list_builtin_packs()) == len(BUILTIN_PACKS)


@pytest.mark.unit
class TestLoadPack:
    """Tests for load_pack()."""

    @pytest.mark.parametrize("name", sorted(BUILTIN_PACKS))
    def test_loads_each_builtin(self, name: str) -> None:
        loaded = load_pack(name)
        assert loaded.template is not None
        assert loaded.raw_yaml
        assert loaded.source_name

    def test_not_found_raises(self) -> None:
        with pytest.raises(TemplateNotFoundError, match="no-such-pack"):
            load_pack("no-such-pack")

    @pytest.mark.parametrize("name", ["../etc/passwd", "foo/bar", "a\\b", ".hidden"])
    def test_invalid_name_rejected(self, name: str) -> None:
        with pytest.raises(TemplateNotFoundError, match="must match"):
            load_pack(name)

    def test_case_insensitive(self) -> None:
        loaded = load_pack("Security-Team")
        assert loaded.template.metadata.name == "Security Team"

    def test_user_override(self, tmp_path: Path) -> None:
        """User pack overrides builtin of the same name."""
        user_yaml = """\
template:
  name: "User Security"
  description: "Custom security pack"
  version: "1.0.0"

  company:
    type: "custom"

  agents:
    - role: "Security Engineer"
      name: "Custom Agent"
      model: "medium"
      department: "security"
"""
        pack_file = tmp_path / "security-team.yaml"
        pack_file.write_text(user_yaml, encoding="utf-8")

        with patch(
            "synthorg.templates.pack_loader._USER_PACKS_DIR",
            tmp_path,
        ):
            loaded = load_pack("security-team")
            assert loaded.template.metadata.name == "User Security"


@pytest.mark.unit
class TestListPacks:
    """Tests for list_packs()."""

    def test_returns_pack_info_instances(self) -> None:
        packs = list_packs()
        assert all(isinstance(p, PackInfo) for p in packs)

    def test_returns_sorted_by_name(self) -> None:
        packs = list_packs()
        names = [p.name for p in packs]
        assert names == sorted(names)

    def test_all_builtins_present(self) -> None:
        packs = list_packs()
        names = {p.name for p in packs}
        assert set(BUILTIN_PACKS) <= names


@pytest.mark.unit
class TestBuiltinPacksValidity:
    """Validate that all built-in packs are well-formed."""

    @pytest.mark.parametrize("name", sorted(BUILTIN_PACKS))
    def test_valid_schema(self, name: str) -> None:
        loaded = load_pack(name)
        assert loaded.template.metadata.name
        assert loaded.template.metadata.description

    @pytest.mark.parametrize("name", sorted(BUILTIN_PACKS))
    def test_has_agents_and_departments(self, name: str) -> None:
        loaded = load_pack(name)
        assert len(loaded.template.agents) >= 1
        assert len(loaded.template.departments) >= 1

    @pytest.mark.parametrize("name", sorted(BUILTIN_PACKS))
    def test_agents_have_departments(self, name: str) -> None:
        loaded = load_pack(name)
        dept_names = {d.name.lower() for d in loaded.template.departments}
        for agent in loaded.template.agents:
            assert agent.department is not None
            assert agent.department.lower() in dept_names

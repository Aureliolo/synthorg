"""Tests for scripts/generate_endpoint_table.py."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _import_script() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "generate_endpoint_table.py"
    )
    spec = importlib.util.spec_from_file_location("generate_endpoint_table", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _import_script()

pytestmark = pytest.mark.unit


class TestStripPrefix:
    """Tests for ``_strip_prefix``."""

    def test_strips_api_v1_prefix(self) -> None:
        assert gen._strip_prefix("/api/v1/agents") == "/agents"

    def test_returns_root_when_only_prefix(self) -> None:
        assert gen._strip_prefix("/api/v1") == "/"

    def test_passes_through_unprefixed(self) -> None:
        assert gen._strip_prefix("/healthz") == "/healthz"


class TestCommonBasePath:
    """Tests for ``_common_base_path``."""

    def test_single_path_returned_verbatim(self) -> None:
        assert gen._common_base_path(["/api/v1/agents"]) == "/agents"

    def test_two_paths_share_prefix(self) -> None:
        result = gen._common_base_path(
            ["/api/v1/agents", "/api/v1/agents/{agent_id}"],
        )
        assert result == "/agents"

    def test_disjoint_paths_return_empty(self) -> None:
        result = gen._common_base_path(
            ["/api/v1/agents", "/api/v1/tasks"],
        )
        # No common prefix beyond "/" -- caller must consult
        # ``TAG_BASE_PATH_FALLBACK`` rather than rendering one of the
        # paths as if both lived under it.
        assert result == ""

    def test_empty_input_returns_empty(self) -> None:
        assert gen._common_base_path([]) == ""


class TestSectionForTag:
    """Tests for ``_section_for_tag`` (raises on unknown tags)."""

    def test_known_tag_returns_section(self) -> None:
        assert gen._section_for_tag("agents") == "Organization and agents"

    def test_unknown_tag_raises(self) -> None:
        # An unmapped tag is a documentation bug, not a recoverable
        # condition: failing fast forces the contributor to map the
        # new controller before the rendered table ships.
        with pytest.raises(KeyError, match="brand-new-domain"):
            gen._section_for_tag("brand-new-domain")


class TestBuildTable:
    """End-to-end check that the rendered block contains expected rows."""

    def test_renders_three_tags_into_two_sections(self) -> None:
        schema = {
            "paths": {
                "/api/v1/agents": {
                    "get": {"tags": ["agents"]},
                    "post": {"tags": ["agents"]},
                },
                "/api/v1/agents/{agent_id}": {
                    "get": {"tags": ["agents"]},
                },
                "/api/v1/auth/login": {
                    "post": {"tags": ["auth"]},
                },
                "/api/v1/budget": {
                    "get": {"tags": ["budget"]},
                },
            },
        }
        rendered = gen._build_table(schema)
        assert "### Identity and users" in rendered
        assert "### Organization and agents" in rendered
        assert "### Operations and platform" in rendered
        assert "| Auth | `/auth/login` | Auth endpoint. |" in rendered
        assert "Agents" in rendered
        assert "Budget" in rendered

    def test_skips_non_tag_keys(self) -> None:
        schema = {
            "paths": {
                "/api/v1/agents": {
                    "summary": "ignored",
                    "parameters": [],
                    "get": {"tags": ["agents"]},
                },
            },
        }
        rendered = gen._build_table(schema)
        assert "Agents" in rendered

    def test_dedupes_tag_per_path_across_verbs(self) -> None:
        # The same tag listed on GET + POST should not double-count
        # the path.
        schema = {
            "paths": {
                "/api/v1/agents": {
                    "get": {"tags": ["agents"]},
                    "post": {"tags": ["agents"]},
                    "delete": {"tags": ["agents"]},
                },
            },
        }
        rendered = gen._build_table(schema)
        assert "Agents endpoint." in rendered  # 1 path, not "3 routes"


class TestReplaceBlock:
    """Tests for ``_replace_block``."""

    def test_replaces_between_sentinels(self) -> None:
        before = f"head\n{gen.BEGIN_SENTINEL}\nold content\n{gen.END_SENTINEL}\ntail"
        result = gen._replace_block(before, "fresh\n")
        assert "old content" not in result
        assert "fresh" in result
        assert result.startswith("head")
        assert result.endswith("tail")

    def test_missing_sentinels_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Could not find sentinels"):
            gen._replace_block("no sentinels here", "x")

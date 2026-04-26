"""Tests for scripts/generate_comparison.py."""

import datetime as dt
import importlib.util
import subprocess
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml


def _import_script() -> ModuleType:
    """Import generate_comparison.py as a module."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "generate_comparison.py"
    spec = importlib.util.spec_from_file_location("generate_comparison", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _import_script()


# Fixtures


DIMS: list[dict[str, str]] = [
    {"key": "memory", "label": "Memory", "description": "Mem"},
    {"key": "tool_use", "label": "Tool Use", "description": "Tools"},
    {"key": "org_structure", "label": "Org Structure", "description": ""},
    {"key": "multi_agent", "label": "Multi-Agent", "description": ""},
    {"key": "task_delegation", "label": "Task Delegation", "description": ""},
    {"key": "human_in_loop", "label": "HITL", "description": ""},
    {"key": "budget_tracking", "label": "Budget", "description": ""},
    {"key": "security_model", "label": "Security", "description": ""},
    {"key": "observability", "label": "Observability", "description": ""},
    {"key": "web_dashboard", "label": "Dashboard", "description": ""},
    {"key": "cli", "label": "CLI", "description": ""},
    {"key": "production_ready", "label": "Prod Ready", "description": ""},
    {"key": "workflow_types", "label": "Workflows", "description": ""},
    {"key": "template_system", "label": "Templates", "description": ""},
]

CATS: list[dict[str, str]] = [
    {"key": "framework", "label": "Multi-Agent Framework"},
]

COMP: dict[str, Any] = {
    "name": "TestFramework",
    "slug": "test-framework",
    "url": "https://example.com",
    "description": "A test framework",
    "license": "MIT",
    "language": "Python",
    "category": "framework",
    "features": {
        "memory": {"support": "full", "note": "Built-in memory"},
        "tool_use": {"support": "partial", "note": "Limited tools"},
    },
}

MINIMAL_YAML: dict[str, Any] = {
    "meta": {"last_updated": "2026-04-02"},
    "dimensions": DIMS,
    "categories": CATS,
    "competitors": [COMP],
}


@pytest.fixture
def minimal_yaml_file(tmp_path: Path) -> Generator[Path]:
    """Write minimal valid YAML and patch DATA_FILE."""
    f = tmp_path / "competitors.yaml"
    f.write_text(yaml.dump(MINIMAL_YAML), encoding="utf-8")
    with patch.object(gen, "DATA_FILE", f):
        yield f


def _write_yaml(
    tmp_path: Path,
    data: Any,
    name: str = "test.yaml",
) -> Path:
    f = tmp_path / name
    f.write_text(yaml.dump(data), encoding="utf-8")
    return f


# _load_data


@pytest.mark.unit
class TestLoadData:
    """Tests for _load_data validation logic."""

    def test_valid_data(self, minimal_yaml_file: Path) -> None:
        data = gen._load_data()
        assert data["meta"]["last_updated"] == "2026-04-02"
        assert len(data["competitors"]) == 1
        assert data["competitors"][0]["name"] == "TestFramework"

    def test_missing_file(self, tmp_path: Path) -> None:
        with (
            patch.object(gen, "DATA_FILE", tmp_path / "nope.yaml"),
            pytest.raises(FileNotFoundError, match="Data file not found"),
        ):
            gen._load_data()

    def test_empty_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match="empty or contains no data"),
        ):
            gen._load_data()

    def test_missing_top_level_keys(self, tmp_path: Path) -> None:
        data = {"meta": {"last_updated": "2026-01-01"}}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match="Missing top-level keys"),
        ):
            gen._load_data()

    def test_empty_competitors_list(self, tmp_path: Path) -> None:
        data = {**MINIMAL_YAML, "competitors": []}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match="No competitors found"),
        ):
            gen._load_data()

    def test_missing_last_updated(self, tmp_path: Path) -> None:
        data = {**MINIMAL_YAML, "meta": {}}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match=r"Missing meta\.last_updated"),
        ):
            gen._load_data()

    def test_competitor_missing_name(self, tmp_path: Path) -> None:
        bad = {"slug": "x", "category": "framework"}
        data = {**MINIMAL_YAML, "competitors": [bad]}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match=r"missing required keys.*name"),
        ):
            gen._load_data()

    def test_competitor_missing_slug(self, tmp_path: Path) -> None:
        bad = {"name": "Test", "category": "framework"}
        data = {**MINIMAL_YAML, "competitors": [bad]}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(ValueError, match=r"missing required keys.*slug"),
        ):
            gen._load_data()

    def test_competitor_not_a_mapping(self, tmp_path: Path) -> None:
        data = {**MINIMAL_YAML, "competitors": ["not-a-dict"]}
        f = _write_yaml(tmp_path, data)
        with (
            patch.object(gen, "DATA_FILE", f),
            pytest.raises(TypeError, match="not a mapping"),
        ):
            gen._load_data()


_FROZEN_DATE = "2026-04-26"


def _patch_today(date_str: str = _FROZEN_DATE) -> Any:
    """Patch dt.datetime.now to a fixed UTC date so the fallback is deterministic.

    Eliminates a race condition where a test that calls `dt.datetime.now`
    in the assertion can disagree with the function's own call if they
    straddle UTC midnight.
    """
    frozen = MagicMock()
    frozen.date.return_value.isoformat.return_value = date_str
    mock_dt = MagicMock()
    mock_dt.datetime.now.return_value = frozen
    mock_dt.UTC = dt.UTC
    return patch.object(gen, "dt", mock_dt)


@pytest.mark.unit
class TestResolveLastUpdated:
    """Tests for _resolve_last_updated git-derived timestamp logic."""

    def test_pinned_date_passes_through(self) -> None:
        assert gen._resolve_last_updated("2026-04-02") == "2026-04-02"

    def test_auto_uses_git_commit_date(self) -> None:
        fake = MagicMock(returncode=0, stdout="2026-04-22\n", stderr="")
        with patch.object(gen.subprocess, "run", return_value=fake):
            assert gen._resolve_last_updated(gen.AUTO_SENTINEL) == "2026-04-22"

    def test_auto_strips_extra_whitespace_from_git_output(self) -> None:
        fake = MagicMock(returncode=0, stdout="  2026-04-22\t\n  ", stderr="")
        with patch.object(gen.subprocess, "run", return_value=fake):
            assert gen._resolve_last_updated(gen.AUTO_SENTINEL) == "2026-04-22"

    def test_auto_returns_unvalidated_git_output(self) -> None:
        # Documents intentional behavior: the resolver trusts git's %cs
        # format and does not validate the string. If git ever returns
        # malformed output, the rendered page surfaces it verbatim
        # (caught by visual review, not this layer).
        fake = MagicMock(returncode=0, stdout="2026-04-22garbage\n", stderr="")
        with patch.object(gen.subprocess, "run", return_value=fake):
            assert gen._resolve_last_updated(gen.AUTO_SENTINEL) == "2026-04-22garbage"

    @pytest.mark.parametrize(
        "side_effect",
        [
            subprocess.CalledProcessError(128, "git"),
            FileNotFoundError(),
            subprocess.TimeoutExpired("git", timeout=10),
        ],
        ids=["called_process_error", "git_missing", "timeout"],
    )
    def test_auto_falls_back_on_git_error(
        self,
        side_effect: BaseException,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with (
            _patch_today(),
            patch.object(gen.subprocess, "run", side_effect=side_effect),
        ):
            assert gen._resolve_last_updated(gen.AUTO_SENTINEL) == _FROZEN_DATE
        # User-visible warning so build environments never silently fall back.
        assert "WARNING" in capsys.readouterr().err

    def test_auto_falls_back_on_empty_output(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        fake = MagicMock(returncode=0, stdout="\n", stderr="")
        with (
            _patch_today(),
            patch.object(gen.subprocess, "run", return_value=fake),
        ):
            assert gen._resolve_last_updated(gen.AUTO_SENTINEL) == _FROZEN_DATE
        # Empty git stdout must be visibly warned about so a contributor
        # sees the fallback fired (matches the exception-path warning).
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "empty stdout" in err


# Helper functions


HELPER_DIMS: list[dict[str, str]] = [
    {"key": "memory", "label": "Memory"},
    {"key": "tool_use", "label": "Tool Use"},
]

HELPER_CATS: list[dict[str, str]] = [
    {"key": "framework", "label": "Multi-Agent Framework"},
    {"key": "platform", "label": "Commercial Platform"},
]


@pytest.mark.unit
class TestHelpers:
    """Tests for helper functions."""

    def test_dimension_label_known(self) -> None:
        assert gen._dimension_label(HELPER_DIMS, "memory") == "Memory"

    def test_dimension_label_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = gen._dimension_label(HELPER_DIMS, "unknown_dim")
        assert result == "unknown_dim"
        assert "WARNING" in capsys.readouterr().err

    def test_category_label_known(self) -> None:
        label = gen._category_label(HELPER_CATS, "framework")
        assert label == "Multi-Agent Framework"

    def test_category_label_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = gen._category_label(HELPER_CATS, "unknown_cat")
        assert result == "unknown_cat"
        assert "WARNING" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("full", "\u2714"),
            ("partial", "~"),
            ("none", "-"),
            ("planned", "\u23f2"),
        ],
    )
    def test_support_icon_known(self, value: str, expected: str) -> None:
        assert gen._support_icon(value) == expected

    def test_support_icon_unknown(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = gen._support_icon("bogus")
        assert result == "bogus"
        assert "WARNING" in capsys.readouterr().err


# Markdown generation


@pytest.mark.unit
class TestMarkdownGeneration:
    """Tests for Markdown output."""

    def test_generate_markdown_structure(self) -> None:
        markdown = gen._generate_markdown(MINIMAL_YAML)
        assert "# Framework Comparison" in markdown
        assert "## Organization & Coordination" in markdown
        assert "## Technical Capabilities" in markdown
        assert "## Operations & Tooling" in markdown
        assert "## Maturity" in markdown
        assert "## Project Links" in markdown

    def test_generate_markdown_contains_competitor(self) -> None:
        markdown = gen._generate_markdown(MINIMAL_YAML)
        assert "TestFramework" in markdown
        assert COMP["url"] in markdown
        assert "MIT" in markdown

    def test_frontmatter_contains_date(self) -> None:
        lines = gen._frontmatter_and_intro("2026-04-02")
        text = "\n".join(lines)
        assert "Last updated: 2026-04-02" in text

    def test_frontmatter_contains_legend(self) -> None:
        lines = gen._frontmatter_and_intro("2026-04-02")
        text = "\n".join(lines)
        assert "Full support" in text
        assert "Partial support" in text

    def test_competitor_row_with_url(self) -> None:
        row = gen._competitor_row(COMP, ["memory", "tool_use"], CATS)
        assert "[TestFramework](https://example.com)" in row
        assert "\u2714" in row  # full support for memory
        assert "~" in row  # partial support for tool_use

    def test_competitor_row_without_url(self) -> None:
        comp = {**COMP, "url": ""}
        row = gen._competitor_row(comp, ["memory"], CATS)
        assert "TestFramework" in row
        assert "[" not in row  # no link

    def test_competitor_row_synthorg_bold(self) -> None:
        comp = {**COMP, "is_synthorg": True}
        row = gen._competitor_row(comp, ["memory"], CATS)
        assert "**TestFramework**" in row

    def test_project_links(self) -> None:
        lines = gen._project_links(MINIMAL_YAML["competitors"])
        text = "\n".join(lines)
        assert "**TestFramework**" in text
        assert "[Website](https://example.com)" in text


# main()


@pytest.mark.unit
class TestMain:
    """Tests for the main() entrypoint."""

    def test_main_success(self, tmp_path: Path, minimal_yaml_file: Path) -> None:
        out = tmp_path / "output.md"
        with (
            patch.object(gen, "OUTPUT_FILE", out),
            patch.object(gen, "REPO_ROOT", tmp_path),
        ):
            result = gen.main()
        assert result == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "# Framework Comparison" in content

    def test_main_missing_data_file(self, tmp_path: Path) -> None:
        with (
            patch.object(gen, "DATA_FILE", tmp_path / "missing.yaml"),
            patch.object(gen, "OUTPUT_FILE", tmp_path / "out.md"),
        ):
            assert gen.main() == 1

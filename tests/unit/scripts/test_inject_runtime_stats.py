"""Tests for scripts/inject_runtime_stats.py.

The injector reads ``data/runtime_stats.yaml`` and rewrites the inner
content of every ``<!--RS:NAME-->...<!--/RS-->`` marker in the in-scope
documentation files. The substitution is idempotent (running twice
produces identical output) and treats unknown marker names as a hard
error to catch typos.
"""

import importlib.util
from collections.abc import Generator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
import yaml


def _import_script() -> ModuleType:
    """Import scripts/inject_runtime_stats.py as a module."""
    script = Path(__file__).resolve().parents[3] / "scripts" / "inject_runtime_stats.py"
    spec = importlib.util.spec_from_file_location("inject_runtime_stats", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inj = _import_script()


_STATS_FIXTURE: dict[str, Any] = {
    "schema_version": 1,
    "last_generated_utc": "2026-05-06T00:00:00Z",
    "generator_revision": "test",
    "stats": {
        "tests": {"raw": 27000, "rounded": 27000, "display": "27,000+"},
        "version": {"raw": "v0.7.1", "display": "v0.7.1"},
        "mem0_stars": {"raw": 54000, "rounded": 54000, "display": "54k+"},
        "providers_curated": {"raw": 19, "display": "19"},
        "providers_via_litellm": {"raw": 100, "display": "100+"},
        "subagents": {"raw": 7, "display": "7"},
    },
    "sources": {},
}


@pytest.fixture
def repo_with_stats(tmp_path: Path) -> Generator[Path]:
    """Yield a tmp REPO_ROOT seeded with data/runtime_stats.yaml."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "runtime_stats.yaml").write_text(
        yaml.safe_dump(_STATS_FIXTURE), encoding="utf-8"
    )
    with (
        patch.object(inj, "REPO_ROOT", tmp_path),
        patch.object(inj, "_STATS_FILE", data_dir / "runtime_stats.yaml"),
    ):
        yield tmp_path


@pytest.mark.unit
class TestRewrite:
    """`rewrite_text` substitutes marker contents."""

    def test_rewrites_single_marker(self) -> None:
        original = "Tested with <!--RS:tests-->OLD<!--/RS--> tests."
        rewritten = inj.rewrite_text(original, _STATS_FIXTURE["stats"])
        assert rewritten == "Tested with <!--RS:tests-->27,000+<!--/RS--> tests."

    def test_idempotent(self) -> None:
        original = "Tested with <!--RS:tests-->27,000+<!--/RS--> tests."
        once = inj.rewrite_text(original, _STATS_FIXTURE["stats"])
        twice = inj.rewrite_text(once, _STATS_FIXTURE["stats"])
        assert once == twice == original

    def test_multiple_markers_per_file(self) -> None:
        original = (
            "Tested with <!--RS:tests-->OLD<!--/RS--> tests across "
            "<!--RS:subagents-->OLD<!--/RS--> agents.\n"
            "Production-ready (<!--RS:mem0_stars-->OLD<!--/RS--> stars)."
        )
        rewritten = inj.rewrite_text(original, _STATS_FIXTURE["stats"])
        assert "<!--RS:tests-->27,000+<!--/RS-->" in rewritten
        assert "<!--RS:subagents-->7<!--/RS-->" in rewritten
        assert "<!--RS:mem0_stars-->54k+<!--/RS-->" in rewritten

    def test_no_markers_unchanged(self) -> None:
        original = "Plain prose without any markers.\n# Heading\n- bullet"
        assert inj.rewrite_text(original, _STATS_FIXTURE["stats"]) == original

    def test_unknown_marker_raises(self) -> None:
        original = "Bad <!--RS:not_a_real_stat-->X<!--/RS--> marker."
        with pytest.raises(inj._UnknownStatError) as exc_info:
            inj.rewrite_text(original, _STATS_FIXTURE["stats"])
        assert exc_info.value.marker_name == "not_a_real_stat"
        assert exc_info.value.issue == inj._UnknownStatError.NOT_FOUND

    def test_marker_missing_display_raises(self) -> None:
        bad_stats = {"tests": {"raw": 1}}  # no 'display' key
        original = "Has <!--RS:tests-->OLD<!--/RS--> only."
        with pytest.raises(inj._UnknownStatError) as exc_info:
            inj.rewrite_text(original, bad_stats)
        assert exc_info.value.marker_name == "tests"
        assert exc_info.value.issue == inj._UnknownStatError.MISSING_DISPLAY

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n  "])
    def test_marker_blank_display_raises(self, blank: str) -> None:
        # Empty / whitespace-only display would inject zero visible content
        # into the marker, leaving the rendered prose silently empty. Reject
        # at lookup time so a buggy fetcher fails the build instead.
        bad_stats = {"tests": {"raw": 1, "display": blank}}
        original = "Has <!--RS:tests-->OLD<!--/RS--> only."
        with pytest.raises(inj._UnknownStatError) as exc_info:
            inj.rewrite_text(original, bad_stats)
        assert exc_info.value.marker_name == "tests"
        assert exc_info.value.issue == inj._UnknownStatError.MISSING_DISPLAY

    def test_unclosed_marker_left_alone(self) -> None:
        # No closing <!--/RS--> -- regex finds no match; text unchanged.
        original = "Tested with <!--RS:tests-->27,000+ tests."
        assert inj.rewrite_text(original, _STATS_FIXTURE["stats"]) == original

    def test_unopened_marker_left_alone(self) -> None:
        # No opening <!--RS:NAME--> -- regex finds no match; text unchanged.
        original = "Tested with 27,000+<!--/RS--> tests."
        assert inj.rewrite_text(original, _STATS_FIXTURE["stats"]) == original

    def test_unicode_marker_name_not_matched(self) -> None:
        # _MARKER_RE constrains marker names to [a-z0-9_]+; non-ASCII rejected.
        original = "Body <!--RS:über-->value<!--/RS--> here."
        assert inj.rewrite_text(original, _STATS_FIXTURE["stats"]) == original

    def test_uppercase_marker_name_not_matched(self) -> None:
        # Marker names are snake_case lowercase; uppercase rejected.
        original = "Body <!--RS:Tests-->value<!--/RS--> here."
        assert inj.rewrite_text(original, _STATS_FIXTURE["stats"]) == original


@pytest.mark.unit
class TestInjectFile:
    """`inject_file` rewrites a single doc in place."""

    def test_writes_only_when_content_changes(self, repo_with_stats: Path) -> None:
        readme = repo_with_stats / "README.md"
        readme.write_text(
            "Tested with <!--RS:tests-->27,000+<!--/RS--> tests.\n",
            encoding="utf-8",
        )
        mtime_before = readme.stat().st_mtime_ns
        changed = inj.inject_file(readme, _STATS_FIXTURE["stats"])
        assert changed is False
        # Content unchanged means file should not be rewritten.
        assert readme.stat().st_mtime_ns == mtime_before

    def test_rewrites_when_content_differs(self, repo_with_stats: Path) -> None:
        readme = repo_with_stats / "README.md"
        readme.write_text(
            "Tested with <!--RS:tests-->OLD<!--/RS--> tests.\n",
            encoding="utf-8",
        )
        changed = inj.inject_file(readme, _STATS_FIXTURE["stats"])
        assert changed is True
        assert "27,000+" in readme.read_text(encoding="utf-8")
        assert "OLD" not in readme.read_text(encoding="utf-8")

    def test_skips_missing_file(self, repo_with_stats: Path) -> None:
        # Missing scoped file is a no-op, not an error.
        absent = repo_with_stats / "missing.md"
        assert inj.inject_file(absent, _STATS_FIXTURE["stats"]) is False


@pytest.mark.unit
class TestMain:
    """`main()` walks `_SCOPED_FILES` and rewrites every marker."""

    def test_main_rewrites_every_scoped_file(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_stats: Path,
    ) -> None:
        readme = repo_with_stats / "README.md"
        roadmap = repo_with_stats / "docs" / "roadmap" / "index.md"
        roadmap.parent.mkdir(parents=True)
        readme.write_text(
            "Tested with <!--RS:tests-->OLD<!--/RS--> tests.\n",
            encoding="utf-8",
        )
        roadmap.write_text(
            "Stars: <!--RS:mem0_stars-->OLD<!--/RS-->.\n",
            encoding="utf-8",
        )
        with patch.object(inj, "_SCOPED_FILES", ("README.md", "docs/roadmap/index.md")):
            assert inj.main() == 0

        assert "27,000+" in readme.read_text(encoding="utf-8")
        assert "54k+" in roadmap.read_text(encoding="utf-8")

        out = capsys.readouterr().out
        assert "README.md" in out
        assert "docs/roadmap/index.md" in out

    def test_main_yaml_missing_returns_one(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        with (
            patch.object(inj, "REPO_ROOT", tmp_path),
            patch.object(inj, "_STATS_FILE", tmp_path / "data" / "runtime_stats.yaml"),
        ):
            assert inj.main() == 1
        err = capsys.readouterr().err
        assert "runtime_stats.yaml" in err

    def test_main_yaml_malformed_returns_one(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        # Malformed YAML escapes yaml.safe_load as YAMLError; _load_stats
        # converts it into a TypeError that main() catches and exits 1 on,
        # with a stderr message naming the file and the parser problem.
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        stats_file = data_dir / "runtime_stats.yaml"
        stats_file.write_text("stats: [unclosed", encoding="utf-8")
        with (
            patch.object(inj, "REPO_ROOT", tmp_path),
            patch.object(inj, "_STATS_FILE", stats_file),
        ):
            assert inj.main() == 1
        err = capsys.readouterr().err
        assert "not valid YAML" in err
        assert "runtime_stats.yaml" in err

    def test_main_unknown_marker_exits_one(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_stats: Path,
    ) -> None:
        readme = repo_with_stats / "README.md"
        readme.write_text(
            "Bad <!--RS:not_a_real_stat-->X<!--/RS--> marker.\n",
            encoding="utf-8",
        )
        with patch.object(inj, "_SCOPED_FILES", ("README.md",)):
            assert inj.main() == 1
        err = capsys.readouterr().err
        assert "README.md" in err
        assert "not_a_real_stat" in err
        assert "issue=not_found" in err

    def test_main_missing_scoped_file_fails_fast(
        self,
        capsys: pytest.CaptureFixture[str],
        repo_with_stats: Path,
    ) -> None:
        # Only the README exists in the tmp tree; the roadmap is missing.
        # A scoped doc that does not exist breaks the docs-freshness contract,
        # so main() must exit non-zero rather than silently skipping the file.
        readme = repo_with_stats / "README.md"
        readme.write_text(
            "Tested with <!--RS:tests-->OLD<!--/RS--> tests.\n",
            encoding="utf-8",
        )
        with patch.object(inj, "_SCOPED_FILES", ("README.md", "docs/roadmap/index.md")):
            assert inj.main() == 1
        captured = capsys.readouterr()
        assert "docs/roadmap/index.md" in captured.err
        assert "not found" in captured.err
        assert "error:" in captured.err
        # 1 file rewritten + 1 missing reported in summary before exit.
        assert "1 missing" in captured.out

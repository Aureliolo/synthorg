"""Tests for scripts/generate_runtime_stats.py.

Covers the generator's offline-tolerance contract:

* Each fetcher's failure preserves the previously-written value and
  emits a structured warning to stderr.
* All fetchers failing leaves the existing YAML's ``stats`` block
  untouched (the schema header/timestamp/revision still update).
* Pure helpers (rounding, display formatting) round-trip the
  documented examples.
* The on-disk YAML round-trips through ``yaml.safe_load`` and
  ``yaml.safe_dump`` with the expected top-level keys.
"""

import importlib.util
import subprocess
from collections.abc import Callable, Generator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml


def _import_script() -> ModuleType:
    """Import scripts/generate_runtime_stats.py as a module."""
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "generate_runtime_stats.py"
    )
    spec = importlib.util.spec_from_file_location("generate_runtime_stats", script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen = _import_script()


@pytest.fixture
def out_file(tmp_path: Path) -> Generator[Path]:
    """Yield a writable tmp path patched in as ``_OUT_FILE``."""
    target = tmp_path / "runtime_stats.yaml"
    with patch.object(gen, "_OUT_FILE", target):
        yield target


def _fake_fetchers(
    overrides: dict[str, Callable[[], dict[str, Any]]] | None = None,
) -> dict[str, Callable[[], dict[str, Any]]]:
    """Build a fetcher dict matching the production contract.

    Defaults return small known values for every stat. Pass *overrides*
    to swap in failing fetchers per stat name.
    """
    base: dict[str, Callable[[], dict[str, Any]]] = {
        "tests": lambda: {
            "raw": 17995,
            "rounded": 17000,
            "display": "17,000+",
        },
        "version": lambda: {"raw": "v9.9.9", "display": "v9.9.9"},
        "mem0_stars": lambda: {
            "raw": 54312,
            "rounded": 54000,
            "display": "54k+",
        },
        "providers_curated": lambda: {"raw": 19, "display": "19"},
        "providers_via_litellm": lambda: {"raw": 100, "display": "100+"},
        "subagents": lambda: {"raw": 7, "display": "7"},
    }
    if overrides:
        base.update(overrides)
    return base


@pytest.mark.unit
class TestRoundFloor:
    """`_round_floor` floors to the nearest *step*."""

    def test_floor_to_thousand(self) -> None:
        assert gen._round_floor(17995, 1000) == 17000

    def test_floor_exact_multiple(self) -> None:
        assert gen._round_floor(54000, 1000) == 54000

    def test_floor_below_step_is_zero(self) -> None:
        assert gen._round_floor(123, 1000) == 0

    def test_floor_zero_step_raises(self) -> None:
        with pytest.raises(ValueError, match="step"):
            gen._round_floor(100, 0)


@pytest.mark.unit
class TestFormatHelpers:
    """Display formatters produce the documented strings."""

    def test_thousands_plus(self) -> None:
        assert gen._format_thousands_plus(17000) == "17,000+"
        assert gen._format_thousands_plus(27000) == "27,000+"

    def test_k_plus(self) -> None:
        assert gen._format_k_plus(54000) == "54k+"
        assert gen._format_k_plus(125000) == "125k+"


@pytest.mark.unit
class TestMainHappyPath:
    """All fetchers succeed -> YAML contains every stat with expected display."""

    def test_writes_full_yaml(
        self, out_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.object(gen, "_FETCHERS", _fake_fetchers()):
            assert gen.main() == 0

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == gen._SCHEMA_VERSION
        assert "last_generated_utc" in loaded
        assert "generator_revision" in loaded
        stats = loaded["stats"]
        assert stats["tests"]["display"] == "17,000+"
        assert stats["version"]["display"] == "v9.9.9"
        assert stats["mem0_stars"]["display"] == "54k+"
        assert stats["providers_curated"]["display"] == "19"
        assert stats["providers_via_litellm"]["display"] == "100+"
        assert stats["subagents"]["display"] == "7"
        assert "sources" in loaded
        # Sources mapping is informational; ensure every stat has a source.
        for stat_name in stats:
            assert stat_name in loaded["sources"]

        out = capsys.readouterr().out
        assert "wrote" in out.lower()


@pytest.mark.unit
class TestMainSingleFetcherFails:
    """One fetcher raising preserves its previous value and warns to stderr."""

    def test_preserves_previous_value(
        self, out_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Seed the YAML with prior values.
        seed = {
            "schema_version": gen._SCHEMA_VERSION,
            "last_generated_utc": "2026-01-01T00:00:00Z",
            "generator_revision": "seed",
            "stats": {
                "tests": {
                    "raw": 9000,
                    "rounded": 9000,
                    "display": "9,000+",
                },
                "version": {"raw": "v0.1.0", "display": "v0.1.0"},
                "mem0_stars": {
                    "raw": 1000,
                    "rounded": 1000,
                    "display": "1k+",
                },
                "providers_curated": {"raw": 1, "display": "1"},
                "providers_via_litellm": {"raw": 50, "display": "50+"},
                "subagents": {"raw": 3, "display": "3"},
            },
            "sources": {},
        }
        out_file.write_text(yaml.safe_dump(seed), encoding="utf-8")

        def _raise() -> dict[str, Any]:
            msg = "simulated rate limit"
            raise gen._StatFetchError(msg)

        fetchers = _fake_fetchers({"mem0_stars": _raise})
        with patch.object(gen, "_FETCHERS", fetchers):
            assert gen.main() == 0

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        # Mem0 preserved from seed; others updated from fakes.
        assert loaded["stats"]["mem0_stars"]["display"] == "1k+"
        assert loaded["stats"]["tests"]["display"] == "17,000+"

        err = capsys.readouterr().err
        assert "mem0_stars" in err
        assert "_StatFetchError" in err or "fetch_failed" in err


@pytest.mark.unit
class TestMainAllFetchersFail:
    """Every fetcher failing leaves stats untouched, exit 0."""

    def test_stats_block_unchanged(
        self, out_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        seed = {
            "schema_version": gen._SCHEMA_VERSION,
            "last_generated_utc": "2020-01-01T00:00:00Z",
            "generator_revision": "seed",
            "stats": {
                "tests": {
                    "raw": 1,
                    "rounded": 0,
                    "display": "0+",
                },
            },
            "sources": {},
        }
        out_file.write_text(yaml.safe_dump(seed), encoding="utf-8")

        def _raise() -> dict[str, Any]:
            msg = "offline"
            raise gen._StatFetchError(msg)

        all_failing = dict.fromkeys(gen._FETCHERS, _raise)
        with patch.object(gen, "_FETCHERS", all_failing):
            assert gen.main() == 0

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert loaded["stats"]["tests"]["display"] == "0+"
        # Header still updated though.
        assert loaded["last_generated_utc"] != "2020-01-01T00:00:00Z"

        err = capsys.readouterr().err
        assert "tests" in err


@pytest.mark.unit
class TestFetchSubagents:
    """`_fetch_subagents` counts `.claude/agents/*.md` directly."""

    def test_counts_md_files(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        for name in ("alpha.md", "beta.md", "gamma.md"):
            (agents_dir / name).write_text("---\n", encoding="utf-8")
        # Non-md files ignored.
        (agents_dir / "ignore.txt").write_text("x", encoding="utf-8")

        with patch.object(gen, "REPO_ROOT", tmp_path):
            result = gen._fetch_subagents()
        assert result["raw"] == 3
        assert result["display"] == "3"

    def test_zero_when_directory_missing(self, tmp_path: Path) -> None:
        with (
            patch.object(gen, "REPO_ROOT", tmp_path),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_subagents()


@pytest.mark.unit
class TestFetchVersion:
    """`_fetch_version` calls `gh release list` and parses the tag."""

    def test_extracts_tag(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = '[{"tagName":"v0.7.1"}]\n'
        completed.returncode = 0
        with patch("subprocess.run", return_value=completed) as run:
            result = gen._fetch_version()
        assert result["raw"] == "v0.7.1"
        assert result["display"] == "v0.7.1"
        run.assert_called_once()

    def test_subprocess_failure_raises_stat_fetch_error(self) -> None:
        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["gh"], stderr="boom"),
            ),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_version()

    def test_empty_release_list_raises(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "[]\n"
        completed.returncode = 0
        with (
            patch("subprocess.run", return_value=completed),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_version()


@pytest.mark.unit
class TestFetchMem0Stars:
    """`_fetch_mem0_stars` calls `gh api` and rounds to nearest 1000."""

    def test_rounds_and_formats(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "54312\n"
        completed.returncode = 0
        with patch("subprocess.run", return_value=completed):
            result = gen._fetch_mem0_stars()
        assert result["raw"] == 54312
        assert result["rounded"] == 54000
        assert result["display"] == "54k+"

    def test_non_integer_stdout_raises(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "not a number\n"
        completed.returncode = 0
        with (
            patch("subprocess.run", return_value=completed),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_mem0_stars()


@pytest.mark.unit
class TestFetchTests:
    """`_fetch_tests` parses pytest --collect-only output and rounds."""

    def test_parses_collected_summary(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = (
            "tests/unit/test_a.py::test_one\n"
            "tests/unit/test_a.py::test_two\n"
            "\n17995 tests collected in 4.20s\n"
        )
        completed.returncode = 0
        with patch("subprocess.run", return_value=completed):
            result = gen._fetch_tests()
        assert result["raw"] == 17995
        assert result["rounded"] == 17000
        assert result["display"] == "17,000+"

    def test_no_summary_line_raises(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "no summary here\n"
        completed.returncode = 0
        with (
            patch("subprocess.run", return_value=completed),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_tests()

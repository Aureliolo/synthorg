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
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tests._shared import JsonDict


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
    overrides: dict[str, Callable[[], JsonDict]] | None = None,
) -> dict[str, Callable[[], JsonDict]]:
    """Build a fetcher dict matching the production contract.

    Defaults return small known values for every stat. Pass *overrides*
    to swap in failing fetchers per stat name.
    """
    base: dict[str, Callable[[], JsonDict]] = {
        "tests": lambda: {
            "raw": 17995,
            "rounded": 17000,
            "display": "17,000+",
        },
        "providers_curated": lambda: {"raw": 19, "display": "19"},
        "providers_via_litellm": lambda: {"raw": 100, "display": "100+"},
        "subagents": lambda: {"raw": 7, "display": "7"},
        "convention_gates": lambda: {"raw": 36, "display": "36"},
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
        assert stats["providers_curated"]["display"] == "19"
        assert stats["providers_via_litellm"]["display"] == "100+"
        assert stats["subagents"]["display"] == "7"
        assert stats["convention_gates"]["display"] == "36"
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
                "providers_curated": {"raw": 1, "display": "1"},
                "providers_via_litellm": {"raw": 50, "display": "50+"},
                "subagents": {"raw": 3, "display": "3"},
                "convention_gates": {"raw": 12, "display": "12"},
            },
            "sources": {},
        }
        out_file.write_text(yaml.safe_dump(seed), encoding="utf-8")

        stat_name = "providers_curated"
        source = "synthorg.providers.presets.list_featured_presets"
        reason = "simulated parse failure"

        def _raise() -> JsonDict:
            raise gen._StatFetchError(stat_name, source, reason)

        fetchers = _fake_fetchers({"providers_curated": _raise})
        with patch.object(gen, "_FETCHERS", fetchers):
            assert gen.main() == 0

        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        # providers_curated preserved from seed; others updated from fakes.
        assert loaded["stats"]["providers_curated"]["display"] == "1"
        assert loaded["stats"]["tests"]["display"] == "17,000+"

        err = capsys.readouterr().err
        assert "providers_curated" in err
        assert "fetch_failed" in err
        assert "keeping_prior_value=True" in err
        assert "simulated parse failure" in err


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

        def _make_raiser(stat_name: str) -> Callable[[], JsonDict]:
            def _raise() -> JsonDict:
                raise gen._StatFetchError(stat_name, "any source", "offline")

            return _raise

        all_failing = {name: _make_raiser(name) for name in gen._FETCHERS}
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
class TestFetchConventionGates:
    """`_fetch_convention_gates` counts `scripts/check_*.py` directly."""

    def test_counts_check_scripts(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        for name in ("check_alpha.py", "check_beta.py", "check_gamma.py"):
            (scripts_dir / name).write_text("# stub\n", encoding="utf-8")
        # Non-matching files ignored.
        (scripts_dir / "helper.py").write_text("# stub\n", encoding="utf-8")
        (scripts_dir / "check_README.md").write_text("# stub\n", encoding="utf-8")

        with patch.object(gen, "REPO_ROOT", tmp_path):
            result = gen._fetch_convention_gates()
        assert result["raw"] == 3
        assert result["display"] == "3"

    def test_raises_when_scripts_dir_missing(self, tmp_path: Path) -> None:
        with (
            patch.object(gen, "REPO_ROOT", tmp_path),
            pytest.raises(gen._StatFetchError),
        ):
            gen._fetch_convention_gates()


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


@pytest.mark.unit
class TestStatFetchErrorStructured:
    """`_StatFetchError` carries structured stat_name / source / reason."""

    def test_fields_populated(self) -> None:
        exc = gen._StatFetchError("tests", "pytest", "timed out")
        assert exc.stat_name == "tests"
        assert exc.source == "pytest"
        assert exc.reason == "timed out"
        rendered = str(exc)
        assert "tests" in rendered
        assert "timed out" in rendered


@pytest.mark.unit
class TestFormatHelpersBoundaries:
    """Boundary cases on the rounding-aware display formatters."""

    def test_thousands_plus_zero(self) -> None:
        assert gen._format_thousands_plus(0) == "0+"


@pytest.mark.unit
class TestValidateFetcherSourceParity:
    """`_validate_fetcher_source_parity` catches config drift."""

    def test_clean_config_passes(self) -> None:
        gen._validate_fetcher_source_parity()  # current module state is consistent

    def test_extra_fetcher_raises(self) -> None:
        bogus = dict(gen._FETCHERS)
        bogus["typo_stat"] = lambda: {"raw": 0, "display": "0"}
        with (
            patch.object(gen, "_FETCHERS", bogus),
            pytest.raises(RuntimeError, match="typo_stat"),
        ):
            gen._validate_fetcher_source_parity()

    def test_extra_source_raises(self) -> None:
        bogus = dict(gen._SOURCES)
        bogus["unwired_stat"] = "noop"
        with (
            patch.object(gen, "_SOURCES", bogus),
            pytest.raises(RuntimeError, match="unwired_stat"),
        ):
            gen._validate_fetcher_source_parity()


@pytest.mark.unit
class TestLoadExistingFirstRun:
    """`_load_existing` returns {} when the YAML does not exist."""

    def test_first_run_returns_empty(self, tmp_path: Path) -> None:
        target = tmp_path / "not_yet.yaml"
        with patch.object(gen, "_OUT_FILE", target):
            assert gen._load_existing() == {}

    def test_main_writes_fresh_yaml_on_first_run(self, out_file: Path) -> None:
        assert not out_file.exists()
        with patch.object(gen, "_FETCHERS", _fake_fetchers()):
            assert gen.main() == 0
        loaded = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == gen._SCHEMA_VERSION
        assert "tests" in loaded["stats"]


@pytest.mark.unit
class TestLoadExistingHardFail:
    """`_load_existing` raises on corrupted YAML; never silently empties."""

    def test_yaml_parse_error_raises(self, out_file: Path) -> None:
        out_file.write_text("not: valid: yaml: {[", encoding="utf-8")
        with pytest.raises(RuntimeError, match="not valid YAML"):
            gen._load_existing()

    def test_root_not_mapping_raises(self, out_file: Path) -> None:
        out_file.write_text("- just\n- a list\n", encoding="utf-8")
        with pytest.raises(TypeError, match="not a mapping"):
            gen._load_existing()

    def test_main_exits_one_on_corrupt_yaml(
        self, out_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out_file.write_text(": : :\n", encoding="utf-8")
        assert gen.main() == 1
        err = capsys.readouterr().err
        assert "not valid YAML" in err or "not a mapping" in err


@pytest.mark.unit
class TestValidateStatEntry:
    """`_validate_stat_entry` rejects malformed fetcher results."""

    def test_passes_on_valid_entry(self) -> None:
        gen._validate_stat_entry("tests", {"raw": 1, "display": "1"})

    def test_missing_display_raises(self) -> None:
        with pytest.raises(RuntimeError, match="display"):
            gen._validate_stat_entry("tests", {"raw": 1})

    def test_empty_display_raises(self) -> None:
        with pytest.raises(RuntimeError, match="display"):
            gen._validate_stat_entry("tests", {"raw": 1, "display": ""})

    def test_non_string_display_raises(self) -> None:
        with pytest.raises(RuntimeError, match="display"):
            gen._validate_stat_entry("tests", {"raw": 1, "display": 17000})

    def test_main_exits_one_on_buggy_fetcher(
        self, out_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # out_file fixture patches _OUT_FILE so main() doesn't touch the
        # real data/runtime_stats.yaml when it crashes mid-loop.
        assert not out_file.exists()
        bad: JsonDict = {"raw": 1}  # no display key

        fetchers = _fake_fetchers({"tests": lambda: bad})
        with patch.object(gen, "_FETCHERS", fetchers):
            assert gen.main() == 1
        err = capsys.readouterr().err
        assert "tests" in err
        assert "display" in err


@pytest.mark.unit
class TestGitHead:
    """`_git_head` returns the short sha or 'unknown' with a stderr warning."""

    def test_success_returns_sha(self) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "abc1234\n"
        completed.returncode = 0
        with patch("subprocess.run", return_value=completed):
            assert gen._git_head() == "abc1234"

    def test_called_process_error_warns_and_falls_back(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, ["git"]),
        ):
            assert gen._git_head() == "unknown"
        assert "exited with code 128" in capsys.readouterr().err

    def test_timeout_warns_and_falls_back(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            assert gen._git_head() == "unknown"
        assert "timed out" in capsys.readouterr().err

    def test_file_not_found_warns_and_falls_back(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert gen._git_head() == "unknown"
        assert "not found on PATH" in capsys.readouterr().err

    def test_empty_stdout_warns_and_falls_back(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        completed = MagicMock(spec=subprocess.CompletedProcess)
        completed.stdout = "   \n"
        completed.returncode = 0
        with patch("subprocess.run", return_value=completed):
            assert gen._git_head() == "unknown"
        assert "empty stdout" in capsys.readouterr().err

"""Tests for scripts/check_runtime_stats_freshness.py.

Pins the gate's contract:

* clean YAML (values match recomputed, age <= 14 days) -> exit 0
* any ``stats.<name>.raw`` drift -> exit 1 with structured diff line
* committed ``last_generated_utc`` older than 14 days -> exit 1 with age line
* a fetcher raising ``_StatFetchError`` is treated as "unable to check",
  not as drift
* corrupted YAML hard-fails (exit 1)
* ``--skip-network`` flag bypasses network-backed fetchers entirely
"""

import datetime as dt
import importlib.util
from collections.abc import Callable, Generator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import patch

import pytest
import yaml


def _import_script(name: str) -> ModuleType:
    """Load ``scripts/<name>.py`` as a module, mirroring the sibling tests."""
    script = Path(__file__).resolve().parents[3] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _import_script("check_runtime_stats_freshness")
gen = _import_script("generate_runtime_stats")


def _good_yaml_payload(timestamp_iso: str) -> dict[str, Any]:
    """A YAML mapping whose stats match :func:`_deterministic_fetchers`."""
    return {
        "schema_version": gen._SCHEMA_VERSION,
        "last_generated_utc": timestamp_iso,
        "generator_revision": "test",
        "stats": {
            "tests": {"raw": 17995, "rounded": 17000, "display": "17,000+"},
            "version": {"raw": "v9.9.9", "display": "v9.9.9"},
            "mem0_stars": {"raw": 54312, "rounded": 54000, "display": "54k+"},
            "providers_curated": {"raw": 19, "display": "19"},
            "providers_via_litellm": {"raw": 100, "display": "100+"},
            "subagents": {"raw": 7, "display": "7"},
            "convention_gates": {"raw": 36, "display": "36"},
        },
        "sources": {},
    }


def _deterministic_fetchers() -> dict[str, Callable[[], dict[str, Any]]]:
    """Fetchers that return the values from :func:`_good_yaml_payload`."""
    return {
        "tests": lambda: {"raw": 17995, "rounded": 17000, "display": "17,000+"},
        "version": lambda: {"raw": "v9.9.9", "display": "v9.9.9"},
        "mem0_stars": lambda: {"raw": 54312, "rounded": 54000, "display": "54k+"},
        "providers_curated": lambda: {"raw": 19, "display": "19"},
        "providers_via_litellm": lambda: {"raw": 100, "display": "100+"},
        "subagents": lambda: {"raw": 7, "display": "7"},
        "convention_gates": lambda: {"raw": 36, "display": "36"},
    }


def _iso(days_ago: int) -> str:
    """ISO-8601 UTC timestamp at *days_ago* in the past, ``Z`` suffix."""
    return (
        (dt.datetime.now(dt.UTC) - dt.timedelta(days=days_ago))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@pytest.fixture
def yaml_path(tmp_path: Path) -> Generator[Path]:
    """Yield a writable tmp YAML path patched in as the gate's target."""
    target = tmp_path / "runtime_stats.yaml"
    with patch.object(check, "_YAML_FILE", target):
        yield target


@pytest.fixture
def wired_generator() -> Generator[None]:
    """Point the gate at the test's loaded generator instance.

    The gate imports the generator at module load with its own private
    module object; tests patch :class:`_FETCHERS` on the *test's* loaded
    ``gen`` module, so the gate must consult that same instance.
    """
    with patch.object(check, "GENERATOR_MODULE", gen):
        yield


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestCleanCase:
    """Values match and YAML is recent -> exit 0, no stderr."""

    def test_clean_when_values_match_and_recent(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        yaml_path.write_text(
            yaml.safe_dump(_good_yaml_payload(_iso(1))), encoding="utf-8"
        )
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert captured.err == ""


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestValueDrift:
    """A fetcher returning a different ``raw`` is reported as drift."""

    def test_fails_when_value_drift(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        yaml_path.write_text(
            yaml.safe_dump(_good_yaml_payload(_iso(1))), encoding="utf-8"
        )
        fetchers = _deterministic_fetchers()
        fetchers["tests"] = lambda: {
            "raw": 29963,
            "rounded": 29000,
            "display": "29,000+",
        }
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "stats.tests.raw drift" in err
        assert "29963" in err
        assert "17995" in err
        assert "scripts/generate_runtime_stats.py" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestStaleAge:
    """A 30-day-old ``last_generated_utc`` trips the age ceiling."""

    def test_fails_when_stale_timestamp(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        stale_iso = _iso(30)
        yaml_path.write_text(
            yaml.safe_dump(_good_yaml_payload(stale_iso)), encoding="utf-8"
        )
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "last regenerated" in err
        assert stale_iso[:10] in err
        assert "14d" in err
        assert "scripts/generate_runtime_stats.py" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestOfflineToleranceForFailingFetcher:
    """``_StatFetchError`` from a fetcher is "unable to check", not drift."""

    def test_failing_fetcher_does_not_fail_gate(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        yaml_path.write_text(
            yaml.safe_dump(_good_yaml_payload(_iso(1))), encoding="utf-8"
        )
        fetchers = _deterministic_fetchers()

        stat_name = "mem0_stars"

        def _raise() -> dict[str, Any]:
            raise gen._StatFetchError(stat_name, "gh api", "rate limited")

        fetchers["mem0_stars"] = _raise
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 0
        captured = capsys.readouterr()
        # Informational note on stderr; no actual drift violation reported.
        assert "stats.mem0_stars.raw drift" not in captured.err
        assert "note: skipping drift check for mem0_stars" in captured.err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestCorruptYAML:
    """Malformed YAML hard-fails with exit 1; never silent pass."""

    def test_corrupted_yaml_hard_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        yaml_path.write_text("not: valid: yaml: {[", encoding="utf-8")
        assert check.main([]) == 1
        err = capsys.readouterr().err.lower()
        assert "could not parse" in err or "yaml" in err

    def test_missing_yaml_hard_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Fixture creates the path but never writes to it.
        assert not yaml_path.exists()
        assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "does not exist" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestSkipNetworkFlag:
    """``--skip-network`` bypasses fetchers in ``_NETWORK_STATS``."""

    def test_skip_network_short_circuits_network_fetchers(
        self,
        yaml_path: Path,
    ) -> None:
        yaml_path.write_text(
            yaml.safe_dump(_good_yaml_payload(_iso(1))), encoding="utf-8"
        )
        called: list[str] = []
        fetchers = _deterministic_fetchers()
        skip_reason = "should not have run"

        def _make_trap(name: str, source: str) -> Callable[[], dict[str, Any]]:
            def _trap() -> dict[str, Any]:
                called.append(name)
                raise gen._StatFetchError(name, source, skip_reason)

            return _trap

        fetchers["tests"] = _make_trap("tests", "subprocess pytest")
        fetchers["version"] = _make_trap("version", "gh release")
        fetchers["mem0_stars"] = _make_trap("mem0_stars", "gh api")
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main(["--skip-network"]) == 0
        assert called == []


@pytest.mark.unit
class TestNetworkStatsInventory:
    """``_NETWORK_STATS`` covers every subprocess-backed fetcher."""

    def test_network_stats_subset_of_fetchers(self) -> None:
        unknown = check._NETWORK_STATS - set(gen._FETCHERS)
        assert not unknown, f"_NETWORK_STATS references unknown stats: {unknown}"

    def test_network_stats_includes_subprocess_fetchers(self) -> None:
        # Hard-pin the known network-backed stats; if the generator
        # adds another subprocess fetcher, this test enforces a
        # deliberate decision on whether to add it to the network set.
        expected = frozenset({"tests", "version", "mem0_stars"})
        assert expected == check._NETWORK_STATS

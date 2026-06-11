"""Tests for scripts/check_runtime_stats_freshness.py.

Pins the gate's contract:

* clean YAML (values match recomputed, age <= 14 days) -> exit 0
* any ``stats.<name>.display`` drift -> exit 1 with structured diff line
* committed ``last_generated_utc`` older than 14 days -> exit 1 with age line
* a fetcher raising ``_StatFetchError`` is treated as "unable to check",
  not as drift
* corrupted YAML hard-fails (exit 1)
* ``--skip-network`` flag bypasses network-backed fetchers entirely
"""

import datetime as dt
import importlib.util
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
import yaml

from tests._shared import JsonDict


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


def _good_yaml_payload(timestamp_iso: str) -> JsonDict:
    """A YAML mapping whose stats match :func:`_deterministic_fetchers`."""
    return {
        "schema_version": gen._SCHEMA_VERSION,
        "last_generated_utc": timestamp_iso,
        "generator_revision": "test",
        "stats": {
            "tests": {"raw": 17995, "rounded": 17000, "display": "17,000+"},
            "providers_curated": {"raw": 19, "display": "19"},
            "providers_via_litellm": {"raw": 100, "display": "100+"},
            "subagents": {"raw": 7, "display": "7"},
            "convention_gates": {"raw": 36, "display": "36"},
        },
        "sources": {},
    }


def _deterministic_fetchers() -> dict[str, Callable[[], JsonDict]]:
    """Fetchers that return the values from :func:`_good_yaml_payload`."""
    return {
        "tests": lambda: {"raw": 17995, "rounded": 17000, "display": "17,000+"},
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


def _seed_fresh_yaml(yaml_path: Path) -> None:
    """Write a 1-day-old YAML with the canonical deterministic stats."""
    yaml_path.write_text(yaml.safe_dump(_good_yaml_payload(_iso(1))), encoding="utf-8")


@pytest.fixture
def yaml_path(tmp_path: Path) -> Iterator[Path]:
    """Yield a writable tmp YAML path patched in as the gate's target."""
    target = tmp_path / "runtime_stats.yaml"
    with patch.object(check, "_YAML_FILE", target):
        yield target


@pytest.fixture
def wired_generator() -> Iterator[None]:
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
        _seed_fresh_yaml(yaml_path)
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert captured.err == ""


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestValueDrift:
    """A fetcher returning a different ``display`` is reported as drift."""

    def test_fails_when_value_drift(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _seed_fresh_yaml(yaml_path)
        fetchers = _deterministic_fetchers()
        fetchers["tests"] = lambda: {
            "raw": 29963,
            "rounded": 29000,
            "display": "29,000+",
        }
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "stats.tests.display drift" in err
        assert "29,000+" in err
        assert "17,000+" in err
        assert "scripts/generate_runtime_stats.py" in err

    def test_raw_only_drift_does_not_trip_gate(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Raw drift with stable display does not trip the gate.

        Rounded counters (test count, etc.) flap on ``raw`` between push
        and CI run; only the rounded ``display`` value appears in docs, so
        only ``display`` drift is actionable.
        """
        _seed_fresh_yaml(yaml_path)
        fetchers = _deterministic_fetchers()
        fetchers["tests"] = lambda: {
            "raw": 17999,
            "rounded": 17000,
            "display": "17,000+",
        }
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 0
        err = capsys.readouterr().err
        assert "drift" not in err


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
        _seed_fresh_yaml(yaml_path)
        fetchers = _deterministic_fetchers()

        stat_name = "providers_via_litellm"

        def _raise() -> JsonDict:
            raise gen._StatFetchError(stat_name, "import litellm", "not importable")

        fetchers["providers_via_litellm"] = _raise
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 0
        captured = capsys.readouterr()
        # Informational note on stderr; no actual drift violation reported.
        assert "stats.providers_via_litellm.display drift" not in captured.err
        assert "note: skipping drift check for providers_via_litellm" in captured.err


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
        _seed_fresh_yaml(yaml_path)
        called: list[str] = []
        fetchers = _deterministic_fetchers()
        skip_reason = "should not have run"

        def _make_trap(name: str, source: str) -> Callable[[], JsonDict]:
            def _trap() -> JsonDict:
                called.append(name)
                raise gen._StatFetchError(name, source, skip_reason)

            return _trap

        fetchers["tests"] = _make_trap("tests", "subprocess pytest")
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
        expected = frozenset({"tests"})
        assert expected == check._NETWORK_STATS


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestMalformedTimestamp:
    """``last_generated_utc`` shape errors fail with a clear age-line."""

    def test_non_string_timestamp_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = _good_yaml_payload(_iso(1))
        payload["last_generated_utc"] = 12345
        yaml_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "missing or non-string" in err
        assert "scripts/generate_runtime_stats.py" in err

    def test_malformed_iso_timestamp_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = _good_yaml_payload(_iso(1))
        payload["last_generated_utc"] = "2026-13-45T99:99:99Z"
        yaml_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "invalid" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestMalformedStatsBlock:
    """Shape errors inside the ``stats`` block surface cleanly."""

    def test_stats_block_not_a_mapping_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = _good_yaml_payload(_iso(1))
        payload["stats"] = ["not", "a", "mapping"]
        yaml_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "'stats' is not a mapping" in err

    def test_committed_entry_not_a_mapping_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        payload = _good_yaml_payload(_iso(1))
        payload["stats"]["tests"] = 17995  # raw int instead of nested dict
        yaml_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        with patch.object(gen, "_FETCHERS", _deterministic_fetchers()):
            assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "stats.tests missing from committed YAML" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestEmptyYAML:
    """An empty (but present) YAML file is treated as corrupt."""

    def test_empty_yaml_hard_fails(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        yaml_path.write_text("", encoding="utf-8")
        assert check.main([]) == 1
        err = capsys.readouterr().err
        assert "is empty" in err or "could not parse" in err


@pytest.mark.unit
@pytest.mark.usefixtures("wired_generator")
class TestFetcherUnexpectedException:
    """A fetcher raising a non-``_StatFetchError`` exception is skipped, not crashed."""

    def test_unexpected_exception_skips_stat_and_continues(
        self,
        yaml_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _seed_fresh_yaml(yaml_path)
        fetchers = _deterministic_fetchers()

        def _raise_runtime() -> JsonDict:
            msg = "unexpected boom"
            raise RuntimeError(msg)

        fetchers["providers_via_litellm"] = _raise_runtime
        with patch.object(gen, "_FETCHERS", fetchers):
            assert check.main([]) == 0
        captured = capsys.readouterr()
        assert "stats.providers_via_litellm.display drift" not in captured.err
        assert "providers_via_litellm" in captured.err
        assert "RuntimeError" in captured.err

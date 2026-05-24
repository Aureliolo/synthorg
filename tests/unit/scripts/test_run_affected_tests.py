# mypy: disable-error-code="explicit-any"
"""Unit tests for the regression guard in ``scripts/run_affected_tests.py``.

Covers ``_check_timing_regression`` and its helpers
(``_load_baseline_snapshot``, ``_check_per_test_regression``,
``_check_env_cap``), the isolation-gate output classifier, the banner
emitter, the ``_run_isolation_gate`` orchestrator, and the
``event_loop_policy`` fixture wired by ``tests/unit/conftest.py``.
Loads the script as a module so the private helpers are callable.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_affected_tests.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_run_affected_tests",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ``_MODULE`` is loaded once at import time and shared across all tests.
# Isolation is preserved because every test mutates module attributes
# via ``monkeypatch`` (auto-reverted at function teardown) rather than
# touching them directly. The script is loaded dynamically so its
# private helpers have no static type; ``cast(Any, ...)`` types the
# handle once here instead of an ``# type: ignore[attr-defined]`` on
# every access site.
_MODULE = cast(Any, _load_script_module())


def _write_baseline(
    tmp_path: Path,
    *,
    unit_suite_seconds: float = 100.0,
    test_count: int = 10_000,
    per_test_ms: float | None = None,
    regression_threshold_ratio: float | None = None,
) -> Path:
    """Write a baseline JSON file under *tmp_path* and return its path."""
    payload: dict[str, object] = {
        "unit_suite_seconds": unit_suite_seconds,
        "test_count": test_count,
    }
    if per_test_ms is not None:
        payload["per_test_ms"] = per_test_ms
    if regression_threshold_ratio is not None:
        payload["regression_threshold_ratio"] = regression_threshold_ratio
    baseline_path = tmp_path / "unit_timing.json"
    baseline_path.write_text(json.dumps(payload), encoding="utf-8")
    return baseline_path


def _patch_baseline(
    monkeypatch: pytest.MonkeyPatch,
    baseline_path: Path,
) -> None:
    """Point the script's baseline path at *baseline_path*."""
    monkeypatch.setattr(_MODULE, "_BASELINE_PATH", baseline_path)


# ── per-test rail ────────────────────────────────────────────────


def test_per_test_regression_fires_at_1_5x_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-test cost grew 1.5x while count stayed flat: guard fires."""
    # Baseline: 100s / 10000 tests = 10ms per test
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=100.0,
            test_count=10_000,
            regression_threshold_ratio=1.3,
        ),
    )
    # Current: 150s / 10000 tests = 15ms per test (1.5x baseline -> trips 1.3x)
    assert _MODULE._check_timing_regression(
        elapsed=150.0,
        run_all=True,
        test_count=10_000,
    )


def test_per_test_regression_does_not_fire_at_20pct_count_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test count grew 20%; per-test cost flat. Guard does NOT fire."""
    # Baseline: 100s / 10000 tests = 10ms per test
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=100.0,
            test_count=10_000,
            regression_threshold_ratio=1.3,
        ),
    )
    # Current: 120s / 12000 tests = 10ms per test (no per-test regression)
    assert not _MODULE._check_timing_regression(
        elapsed=120.0,
        run_all=True,
        test_count=12_000,
    )


def test_per_test_regression_does_not_fire_at_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaying baseline values exactly does not fire the guard."""
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=100.0,
            test_count=10_000,
        ),
    )
    assert not _MODULE._check_timing_regression(
        elapsed=100.0,
        run_all=True,
        test_count=10_000,
    )


def test_per_test_regression_uses_explicit_per_test_ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``per_test_ms`` is given directly, it overrides derived value."""
    # Explicit per_test_ms = 5ms (much faster than 100s/10000=10ms derived)
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=100.0,
            test_count=10_000,
            per_test_ms=5.0,
            regression_threshold_ratio=1.3,
        ),
    )
    # Current: 80s / 10000 = 8ms.  Against derived (10ms) baseline this
    # would NOT trip; against explicit 5ms it WOULD (8 > 5*1.3=6.5).
    assert _MODULE._check_timing_regression(
        elapsed=80.0,
        run_all=True,
        test_count=10_000,
    )


def test_per_test_regression_skips_when_test_count_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a runtime test count, per-test rail abstains (env cap still fires)."""
    _patch_baseline(monkeypatch, _write_baseline(tmp_path))
    assert not _MODULE._check_timing_regression(
        elapsed=999.0,
        run_all=True,
        test_count=None,
    )


# ── env cap ──────────────────────────────────────────────────────


def test_env_cap_overrides_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``UNIT_SUITE_MAX_SECONDS`` fires when elapsed exceeds it."""
    _patch_baseline(monkeypatch, _write_baseline(tmp_path))
    monkeypatch.setenv("UNIT_SUITE_MAX_SECONDS", "10")
    assert _MODULE._check_timing_regression(
        elapsed=15.0,
        run_all=True,
        test_count=10_000,
    )


def test_env_cap_does_not_fire_below_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env cap above elapsed leaves per-test rail in charge."""
    _patch_baseline(monkeypatch, _write_baseline(tmp_path))
    monkeypatch.setenv("UNIT_SUITE_MAX_SECONDS", "1000")
    assert not _MODULE._check_timing_regression(
        elapsed=100.0,
        run_all=True,
        test_count=10_000,
    )


# ── orchestrator guards ──────────────────────────────────────────


def test_skips_when_run_all_is_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Affected-only runs do not compare against the baseline."""
    _patch_baseline(monkeypatch, _write_baseline(tmp_path))
    assert not _MODULE._check_timing_regression(
        elapsed=10_000.0,
        run_all=False,
        test_count=100,
    )


def test_skips_when_baseline_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing baseline disables the guard cleanly."""
    monkeypatch.setattr(_MODULE, "_BASELINE_PATH", tmp_path / "missing.json")
    assert not _MODULE._check_timing_regression(
        elapsed=10_000.0,
        run_all=True,
        test_count=10_000,
    )


def test_raises_when_baseline_is_malformed_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed JSON surfaces a ``BaselineMalformedError`` loudly.

    The previous behaviour silently disabled the guard on a typo, which
    defeated the very class of error the rail exists to catch.  A
    corrupt baseline must surface so the operator fixes the file
    instead of pushing without the regression check.
    """
    from tests.baselines.loader import BaselineMalformedError

    bad = tmp_path / "unit_timing.json"
    bad.write_text("not json", encoding="utf-8")
    _patch_baseline(monkeypatch, bad)
    with pytest.raises(BaselineMalformedError):
        _MODULE._check_timing_regression(
            elapsed=10_000.0,
            run_all=True,
            test_count=10_000,
        )


def test_raises_when_baseline_missing_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Baseline missing ``test_count`` raises rather than silently skipping.

    A baseline that exists but is incomplete is a typo signal -- the
    regression guard must fail loud so the operator restores the
    missing field.
    """
    from tests.baselines.loader import BaselineMalformedError

    bad = tmp_path / "unit_timing.json"
    bad.write_text(json.dumps({"unit_suite_seconds": 100.0}), encoding="utf-8")
    _patch_baseline(monkeypatch, bad)
    with pytest.raises(BaselineMalformedError):
        _MODULE._check_timing_regression(
            elapsed=200.0,
            run_all=True,
            test_count=10_000,
        )


# ── snapshot loader (positive coverage) ──────────────────────────


def test_load_baseline_snapshot_returns_explicit_per_test_ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Snapshot prefers an explicit ``per_test_ms`` over the derived one.

    Regression guard: a JSON field rename that drops ``per_test_ms``
    silently (e.g. typo to ``per_test_milliseconds``) would otherwise
    fall back to the derived value and quietly tighten or loosen the
    rail. This test pins the explicit-field shape.
    """
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=100.0,
            test_count=10_000,
            per_test_ms=4.5,
            regression_threshold_ratio=1.5,
        ),
    )
    snapshot = _MODULE._load_baseline_snapshot()
    assert snapshot is not None
    assert snapshot.per_test_ms == pytest.approx(4.5)
    assert snapshot.threshold_ratio == pytest.approx(1.5)
    assert snapshot.baseline_test_count == 10_000


def test_load_baseline_snapshot_derives_per_test_ms_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``per_test_ms`` the loader derives it from ``unit_suite_seconds``."""
    _patch_baseline(
        monkeypatch,
        _write_baseline(
            tmp_path,
            unit_suite_seconds=200.0,
            test_count=10_000,
        ),
    )
    snapshot = _MODULE._load_baseline_snapshot()
    assert snapshot is not None
    # 200s * 1000 / 10000 = 20.0 ms per test
    assert snapshot.per_test_ms == pytest.approx(20.0)
    # Default threshold ratio when omitted.
    assert snapshot.threshold_ratio == pytest.approx(1.3)
    assert snapshot.baseline_test_count == 10_000


# ── isolation-gate output classifier ─────────────────────────────


_CRASH_LINE = (
    "worker 'gw0' crashed while running "
    "'tests/unit/api/auth/test_postgres_session_store.py"
    "::test_enforce_session_limit_revokes_oldest[2-2]'\n"
)
_CRASH_LINE_OTHER_TEST = (
    "worker 'gw1' crashed while running "
    "'tests/unit/api/controllers/test_meetings.py::test_completed[2-2]'\n"
)
_CRASH_LINE_SAME_TEST_OTHER_ITER = (
    "worker 'gw2' crashed while running "
    "'tests/unit/api/auth/test_postgres_session_store.py"
    "::test_enforce_session_limit_revokes_oldest[1-2]'\n"
)
_FAILED_LINE = (
    "FAILED tests/unit/api/auth/test_csrf.py::test_revealed_state[2-2]"
    " - AssertionError: expected 401, got 200\n"
)


def test_parse_worker_crashes_extracts_worker_test_pairs() -> None:
    """``_parse_worker_crashes`` returns ``(worker, test_id)`` for each line."""
    stdout = _CRASH_LINE + _CRASH_LINE_OTHER_TEST
    crashes = _MODULE._parse_worker_crashes(stdout)
    assert crashes == (
        (
            "gw0",
            "tests/unit/api/auth/test_postgres_session_store.py"
            "::test_enforce_session_limit_revokes_oldest[2-2]",
        ),
        (
            "gw1",
            "tests/unit/api/controllers/test_meetings.py::test_completed[2-2]",
        ),
    )


def test_parse_worker_crashes_returns_empty_on_clean_output() -> None:
    """No ``crashed`` line in stdout returns an empty tuple."""
    stdout = "100 passed in 1.23s\n"
    assert _MODULE._parse_worker_crashes(stdout) == ()


def test_parse_test_failures_extracts_failed_test_ids() -> None:
    """``_parse_test_failures`` returns the test id from each ``FAILED`` line."""
    stdout = _FAILED_LINE + "FAILED tests/unit/foo.py::test_bar - assert 1 == 2\n"
    failures = _MODULE._parse_test_failures(stdout)
    assert failures == (
        "tests/unit/api/auth/test_csrf.py::test_revealed_state[2-2]",
        "tests/unit/foo.py::test_bar",
    )


def test_parse_test_failures_returns_empty_on_clean_output() -> None:
    """No ``FAILED`` line in stdout returns an empty tuple."""
    stdout = "200 passed in 5.67s\n"
    assert _MODULE._parse_test_failures(stdout) == ()


def test_classify_pass_when_returncode_zero_and_no_crashes() -> None:
    """Clean green run -> ``pass`` outcome with exit code 0."""
    outcome = _MODULE._classify_isolation_outcome(
        returncode=0,
        stdout="500 passed in 12.34s\n",
    )
    assert outcome.kind == "pass"
    assert outcome.exit_code == 0
    assert outcome.crashed_tests == ()
    assert outcome.failed_tests == ()
    assert outcome.repeated_crashes == ()


def test_classify_crash_advisory_when_returncode_zero_with_crashes() -> None:
    """xdist recovered (returncode 0) but a worker did crash -> advisory pass."""
    outcome = _MODULE._classify_isolation_outcome(
        returncode=0,
        stdout=_CRASH_LINE + "499 passed, 1 errors in 12.34s\n",
    )
    assert outcome.kind == "crash_advisory"
    assert outcome.exit_code == 0
    assert outcome.crashed_tests == (
        "tests/unit/api/auth/test_postgres_session_store.py"
        "::test_enforce_session_limit_revokes_oldest[2-2]",
    )


def test_classify_regression_when_real_failure_no_crash() -> None:
    """A genuine assertion failure with no worker crash -> regression."""
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=_FAILED_LINE + "499 passed, 1 failed in 12.34s\n",
    )
    assert outcome.kind == "regression"
    assert outcome.exit_code == 1
    assert outcome.failed_tests == (
        "tests/unit/api/auth/test_csrf.py::test_revealed_state[2-2]",
    )
    assert outcome.crashed_tests == ()


def test_classify_regression_when_failure_alongside_crash() -> None:
    """Real failure (FAILED line for a non-crashed test) wins over crashes."""
    stdout = _CRASH_LINE + _FAILED_LINE + "498 passed, 2 failed in 12.34s\n"
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "regression"
    assert outcome.exit_code == 1
    # The failed test is the assertion failure, not the crashed one.
    assert outcome.failed_tests == (
        "tests/unit/api/auth/test_csrf.py::test_revealed_state[2-2]",
    )
    assert len(outcome.crashed_tests) == 1


def test_classify_regression_when_same_test_crashed_twice() -> None:
    """Same logical test crashing on both repeat iterations -> real bug."""
    # Same test, ``[1-2]`` and ``[2-2]`` -- both pytest-repeat iterations crashed.
    stdout = _CRASH_LINE + _CRASH_LINE_SAME_TEST_OTHER_ITER
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "regression"
    assert outcome.exit_code == 1
    # Repeat suffix stripped before counting.
    assert outcome.repeated_crashes == (
        "tests/unit/api/auth/test_postgres_session_store.py"
        "::test_enforce_session_limit_revokes_oldest",
    )


def test_classify_crash_advisory_when_distinct_crashes_only() -> None:
    """Crashes spread across distinct tests, no real failures -> advisory."""
    stdout = _CRASH_LINE + _CRASH_LINE_OTHER_TEST
    # xdist exhausted restarts; returncode is non-zero, but the signal is
    # purely native crashes scattered across unrelated tests.
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "crash_advisory"
    # Advisory: exit 0 even though pytest reported non-zero.
    assert outcome.exit_code == 0
    assert len(outcome.crashed_tests) == 2
    assert outcome.repeated_crashes == ()


def test_classify_filters_crashed_test_from_failed_line() -> None:
    """xdist marks a crashed test ``FAILED``; the classifier strips it."""
    crash_line = (
        "worker 'gw0' crashed while running 'tests/unit/foo.py::test_bar[2-2]'\n"
    )
    failed_line = (
        "FAILED tests/unit/foo.py::test_bar[2-2] - test execution worker died\n"
    )
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=crash_line + failed_line,
    )
    # Only the crash signal survives; the FAILED line is collateral.
    assert outcome.kind == "crash_advisory"
    assert outcome.failed_tests == ()
    assert outcome.crashed_tests == ("tests/unit/foo.py::test_bar[2-2]",)


def test_classify_regression_when_returncode_nonzero_no_signals() -> None:
    """Non-zero exit with no parsable signal -> fail closed (regression)."""
    outcome = _MODULE._classify_isolation_outcome(
        returncode=2,
        stdout="(degraded output, no FAILED or crash lines)\n",
    )
    assert outcome.kind == "regression"
    assert outcome.exit_code == 2
    assert outcome.failed_tests == ()
    assert outcome.repeated_crashes == ()


def test_classify_crash_advisory_when_node_down_with_internal_error() -> None:
    """xdist worker(s) ``node down`` + scheduler ``INTERNALERROR>`` -> advisory.

    Reproduces the Python 3.14 + Windows ProactorEventLoop teardown
    race where workers terminate between tests (so xdist prints ``[gwN]
    node down: Not properly terminated`` instead of the canonical
    ``crashed while running '...'`` form), then the scheduler crashes
    when it tries to reassign work to the dead workers (``KeyError:
    <WorkerController gwN>`` propagated as ``INTERNALERROR>``). Without
    the dedicated branch the classifier would fall through to the
    fail-closed path and block on documented native-level flakiness.
    """
    stdout = (
        "[gw5] node down: Not properly terminated\n"
        "[gw0] node down: Not properly terminated\n"
        "INTERNALERROR> Traceback (most recent call last):\n"
        "INTERNALERROR>   ...xdist scheduler details...\n"
        "INTERNALERROR> KeyError: <WorkerController gw0>\n"
    )
    outcome = _MODULE._classify_isolation_outcome(
        returncode=3,
        stdout=stdout,
    )
    assert outcome.kind == "crash_advisory"
    assert outcome.exit_code == 0
    # Worker ids surface in lieu of test ids (the ``node down`` signature
    # lacks a per-test attribution).
    assert outcome.crashed_tests == ("<worker gw5>", "<worker gw0>")
    assert outcome.failed_tests == ()
    assert outcome.repeated_crashes == ()


def test_classify_crash_advisory_when_node_down_without_internal_error() -> None:
    """``node down`` alone, no ``INTERNALERROR>``, returncode non-zero.

    This is the dominant shape of the documented Python 3.14 + Windows
    xdist teardown crash: the controller-side loadscope crash guard in
    ``tests/conftest.py`` suppresses the downstream ``INTERNALERROR>``,
    and a worker killed mid-teardown dies before pytest prints any
    FAILED summary. Requiring the INTERNALERROR (as the gate once did)
    fails closed on every such crash and blocks every push that widens
    the affected selection. With no real failure and no repeated named
    crash, a bare node-down is advisory; the worker id surfaces in lieu
    of an unrecoverable test id.
    """
    stdout = "[gw5] node down: Not properly terminated\n"
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "crash_advisory"
    assert outcome.exit_code == 0
    assert outcome.crashed_tests == ("<worker gw5>",)
    assert outcome.failed_tests == ()
    assert outcome.repeated_crashes == ()


def test_classify_real_failure_outranks_node_down_signature() -> None:
    """A genuine ``FAILED`` line is never demoted by node-down noise.

    When xdist marks a test ``FAILED`` for a real assertion error AND a
    different worker dies, the real failure is the actionable signal
    and must not be hidden behind the crash-advisory branch.
    """
    stdout = (
        "[gw5] node down: Not properly terminated\n"
        "INTERNALERROR> KeyError: <WorkerController gw5>\n"
        "FAILED tests/unit/foo.py::test_real_failure[2-2] - AssertionError\n"
    )
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "regression"
    assert outcome.failed_tests == ("tests/unit/foo.py::test_real_failure[2-2]",)


def test_classify_advisory_when_single_crash_below_threshold() -> None:
    """A single crash of one test is below the real-bug threshold.

    Proves the boundary directly: if the threshold check ever flips
    from ``>= 2`` to ``>= 1`` the gate would wrongly escalate single
    transient crashes to regressions.
    """
    stdout = _CRASH_LINE  # Single crash, no repeat iteration.
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "crash_advisory"
    assert outcome.exit_code == 0
    assert outcome.repeated_crashes == ()


def test_classify_regression_when_three_iteration_run_all_crash() -> None:
    """Pytest-repeat ``--count 3`` -- all three iterations crash on the same test.

    The classifier strips ``[N-3]`` suffixes the same way as ``[N-2]``,
    so three crashes of the same logical test count as a real bug.
    """
    base = "tests/unit/foo.py::test_bar"
    stdout = "".join(
        f"worker 'gw{i}' crashed while running '{base}[{i + 1}-3]'\n" for i in range(3)
    )
    outcome = _MODULE._classify_isolation_outcome(
        returncode=1,
        stdout=stdout,
    )
    assert outcome.kind == "regression"
    assert outcome.repeated_crashes == (base,)


def test_parse_worker_crashes_ignores_malformed_line() -> None:
    """Lines that don't match the regex are silently skipped, not raised."""
    stdout = (
        "worker gw0 crashed (no quotes around test id)\n"
        "worker 'gw1' completed normally\n"
        "worker 'gw2' crashed while running 'valid_test_id'\n"
    )
    crashes = _MODULE._parse_worker_crashes(stdout)
    assert crashes == (("gw2", "valid_test_id"),)


# ── IsolationOutcome invariant enforcement ───────────────────────


def test_isolation_outcome_pass_with_evidence_raises() -> None:
    """Constructing a ``pass`` outcome with non-empty evidence is rejected."""
    with pytest.raises(ValueError, match="pass outcome"):
        _MODULE.IsolationOutcome(
            kind="pass",
            exit_code=0,
            failed_tests=("tests/foo.py::test_x",),
        )


def test_isolation_outcome_crash_advisory_without_crashes_raises() -> None:
    """``crash_advisory`` requires non-empty ``crashed_tests``."""
    with pytest.raises(ValueError, match="crash_advisory"):
        _MODULE.IsolationOutcome(
            kind="crash_advisory",
            exit_code=0,
        )


def test_isolation_outcome_regression_with_zero_exit_raises() -> None:
    """``regression`` must carry a non-zero exit code."""
    with pytest.raises(ValueError, match="non-zero exit_code"):
        _MODULE.IsolationOutcome(
            kind="regression",
            exit_code=0,
            failed_tests=("tests/foo.py::test_x",),
        )


# ── _print_isolation_banner ──────────────────────────────────────


def test_print_isolation_banner_pass_emits_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``pass`` outcome prints no banner."""
    outcome = _MODULE.IsolationOutcome(kind="pass", exit_code=0)
    _MODULE._print_isolation_banner(outcome)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_print_isolation_banner_regression_failed_tests_blames_state_leak(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression with failed_tests names module-level state leak."""
    outcome = _MODULE.IsolationOutcome(
        kind="regression",
        exit_code=1,
        failed_tests=("tests/unit/foo.py::test_bar",),
    )
    _MODULE._print_isolation_banner(outcome)
    err = capsys.readouterr().err
    assert "Module-level state likely leaked" in err
    assert "tests/unit/foo.py::test_bar" in err


def test_print_isolation_banner_regression_repeated_crash_blames_real_bug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression with repeated_crashes blames a real test bug."""
    outcome = _MODULE.IsolationOutcome(
        kind="regression",
        exit_code=1,
        crashed_tests=(
            "tests/unit/foo.py::test_bar[1-2]",
            "tests/unit/foo.py::test_bar[2-2]",
        ),
        repeated_crashes=("tests/unit/foo.py::test_bar",),
    )
    _MODULE._print_isolation_banner(outcome)
    err = capsys.readouterr().err
    assert "crashed the xdist worker on" in err
    assert "real bug" in err
    assert "tests/unit/foo.py::test_bar" in err


def test_print_isolation_banner_crash_advisory_blames_proactor_race(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Crash advisory blames Windows ProactorEventLoop / cross-worktree."""
    outcome = _MODULE.IsolationOutcome(
        kind="crash_advisory",
        exit_code=0,
        crashed_tests=("tests/unit/foo.py::test_bar[2-2]",),
    )
    _MODULE._print_isolation_banner(outcome)
    err = capsys.readouterr().err
    assert "ADVISORY" in err
    assert "ProactorEventLoop" in err
    assert "concurrent worktrees" in err


def test_print_isolation_banner_regression_no_evidence_uses_failclosed_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail-closed regression (degraded pytest output) emits a generic banner."""
    outcome = _MODULE.IsolationOutcome(
        kind="regression",
        exit_code=2,
    )
    _MODULE._print_isolation_banner(outcome)
    err = capsys.readouterr().err
    assert "could not" in err
    assert "parse" in err
    assert "(2)" in err


# ── _run_isolation_gate orchestrator ─────────────────────────────


def test_run_isolation_gate_skips_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``SYNTHORG_SKIP_ISOLATION_GATE=1`` short-circuits to exit 0."""
    monkeypatch.setenv("SYNTHORG_SKIP_ISOLATION_GATE", "1")
    # _stream_pytest must NOT be invoked; replace with one that fails the test
    # if called.
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd: pytest.fail("_stream_pytest must not run when gate is skipped"),
    )
    rc = _MODULE._run_isolation_gate(["tests/unit/foo/"])
    assert rc == 0
    assert "skipped via SYNTHORG_SKIP_ISOLATION_GATE" in capsys.readouterr().err


def test_run_isolation_gate_skips_when_paths_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty path list short-circuits to exit 0 before running pytest."""
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd: pytest.fail("_stream_pytest must not run on empty paths"),
    )
    assert _MODULE._run_isolation_gate([]) == 0


def test_run_isolation_gate_passes_through_classifier_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Green pytest output -> classifier returns pass -> exit 0."""
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd, **_kwargs: (0, "500 passed in 12.34s\n"),
    )
    assert _MODULE._run_isolation_gate(["tests/unit/foo/"]) == 0


def test_run_isolation_gate_returns_advisory_zero_on_native_crash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Native worker crashes alone -> classifier returns crash_advisory -> exit 0."""
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    stdout = _CRASH_LINE + _CRASH_LINE_OTHER_TEST
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd, **_kwargs: (1, stdout),
    )
    rc = _MODULE._run_isolation_gate(["tests/unit/api/"])
    assert rc == 0
    assert "ADVISORY" in capsys.readouterr().err


def test_run_isolation_gate_returns_nonzero_on_real_regression(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real test failure -> classifier returns regression -> exit non-zero."""
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd, **_kwargs: (1, _FAILED_LINE + "499 passed, 1 failed in 12s\n"),
    )
    rc = _MODULE._run_isolation_gate(["tests/unit/api/"])
    assert rc == 1
    assert "ISOLATION REGRESSION" in capsys.readouterr().err


def test_run_isolation_gate_invokes_pytest_with_correct_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate command embeds ``--count 2`` and ``--max-worker-restart=4``."""
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    captured: dict[str, list[str]] = {}

    def _capture(cmd: list[str], **_kwargs: object) -> tuple[int, str]:
        captured["cmd"] = cmd
        return 0, "1 passed in 0.1s\n"

    monkeypatch.setattr(_MODULE, "_stream_pytest", _capture)
    _MODULE._run_isolation_gate(["tests/unit/foo/"])
    cmd = captured["cmd"]
    assert "--count" in cmd
    assert "2" in cmd
    assert "--max-worker-restart=4" in cmd
    assert "tests/unit/foo/" in cmd


def test_run_pytest_short_circuits_on_hung_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A watchdog kill (returncode == _PYTEST_HUNG_EXIT_CODE) returns 124 immediately.

    The classifier is bypassed -- it would otherwise read worker-crash
    markers in the killed run's stdout and rate the timeout as a
    crash-advisory PASS, letting the push through. The short-circuit
    is the contract that prevents that misclassification.
    """
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd, **_kwargs: (
            _MODULE._PYTEST_HUNG_EXIT_CODE,
            "[gw0] node down: Not properly terminated\n",
        ),
    )

    def _classifier_must_not_run(*_args: object, **_kwargs: object) -> object:
        msg = "classifier must not run on hung-pytest fast path"
        raise AssertionError(msg)

    monkeypatch.setattr(
        _MODULE, "_classify_isolation_outcome", _classifier_must_not_run
    )
    rc = _MODULE._run_pytest(["tests/unit/foo/"])
    assert rc == _MODULE._PYTEST_HUNG_EXIT_CODE
    assert rc == 124


def test_run_isolation_gate_short_circuits_on_hung_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolation gate also short-circuits on watchdog kill (same reasoning).

    A hung ``--count 2`` replay must NOT be classified as
    crash-advisory; that would silently green-light a real isolation
    leak whose run timed out before the replay finished.
    """
    monkeypatch.delenv("SYNTHORG_SKIP_ISOLATION_GATE", raising=False)
    monkeypatch.setattr(
        _MODULE,
        "_stream_pytest",
        lambda _cmd, **_kwargs: (
            _MODULE._PYTEST_HUNG_EXIT_CODE,
            "[gw0] node down: Not properly terminated\n",
        ),
    )

    def _classifier_must_not_run(*_args: object, **_kwargs: object) -> object:
        msg = "classifier must not run on hung-pytest fast path"
        raise AssertionError(msg)

    monkeypatch.setattr(
        _MODULE, "_classify_isolation_outcome", _classifier_must_not_run
    )
    rc = _MODULE._run_isolation_gate(["tests/unit/foo/"])
    assert rc == _MODULE._PYTEST_HUNG_EXIT_CODE
    assert rc == 124


# ── event loop policy fixtures ───────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific policy")
async def test_unit_tier_uses_selector_event_loop_on_windows() -> None:
    """Async tests in the unit tier run under a non-Proactor loop on Windows.

    The unit-tier ``event_loop_policy`` fixture pins
    ``WindowsSelectorEventLoopPolicy``, so pytest-asyncio creates a
    selector-based loop for every async test.  Checking the running
    loop directly catches a regression where the policy fixture
    silently disappeared and tests fell back to the Python default
    ProactorEventLoop -- the exact failure mode the policy guards
    against.
    """
    loop_class_name = type(asyncio.get_running_loop()).__name__
    assert "Proactor" not in loop_class_name, (
        f"unit tier ran under {loop_class_name}; expected a selector-based loop"
    )


# ── worktree-reconciliation guard ────────────────────────────────
# Verifies the pre/post git-status guard that stops the pre-push hook
# reporting "files were modified by this hook": only files the run
# itself dirtied (clean before, changed after) are reverted; the
# developer's real changes are never touched.


class _FakeGit:
    """Records ``_git`` calls and replays scripted ``status`` outputs.

    ``status_outputs`` is consumed in order on each
    ``status --porcelain`` call (snapshot-before, then snapshot-after).
    ``restore`` calls are recorded; set ``restore_raises`` to simulate
    a failed revert.
    """

    def __init__(
        self,
        status_outputs: list[str],
        *,
        restore_raises: bool = False,
        status_raises: bool = False,
    ) -> None:
        self._status_outputs = list(status_outputs)
        self._restore_raises = restore_raises
        self._status_raises = status_raises
        self.calls: list[tuple[str, ...]] = []
        self.status_strip: list[bool] = []

    def __call__(self, *args: str, strip: bool = True) -> str:
        self.calls.append(args)
        if args[:2] == ("status", "--porcelain"):
            self.status_strip.append(strip)
            if self._status_raises:
                msg = "git status unreadable"
                raise _MODULE._GitError(msg)
            if not self._status_outputs:
                return ""
            return self._status_outputs.pop(0)
        if args[0] == "restore":
            if self._restore_raises:
                msg = "restore failed"
                raise _MODULE._GitError(msg)
            return ""
        return ""


def test_tracked_dirty_paths_excludes_untracked_and_parses_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    porcelain = (
        " M src/synthorg/a.py\n"
        "?? scripts/git-hooks/\n"
        "R  old/name.py -> new/name.py\n"
        "A  tests/new_test.py"
    )
    monkeypatch.setattr(_MODULE, "_git", _FakeGit([porcelain]))
    result = _MODULE._tracked_dirty_paths()
    assert result == {
        "src/synthorg/a.py",
        "old/name.py",
        "new/name.py",
        "tests/new_test.py",
    }
    assert "scripts/git-hooks/" not in result


def test_tracked_dirty_paths_requests_unstripped_porcelain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a worktree-only modification's porcelain code is
    # `` M path`` (leading space). When the whole stdout blob is
    # str.strip()-ed, the first line loses that space and the fixed
    # index-3 slice drops the leading character of the path, so the
    # follow-up ``git restore`` fails on a bogus pathspec and the push
    # aborts. The parser MUST request unstripped output.
    fake = _FakeGit([" M scripts/git-hooks/_run-hook.sh\nD  tests/x_test.py"])
    monkeypatch.setattr(_MODULE, "_git", fake)
    result = _MODULE._tracked_dirty_paths()
    assert result == {
        "scripts/git-hooks/_run-hook.sh",
        "tests/x_test.py",
    }
    assert all(p.startswith(("scripts/", "tests/")) for p in result)
    assert fake.status_strip == [False]


def test_reconcile_noop_when_nothing_newly_dirtied(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = _FakeGit([" M kept.py"])
    monkeypatch.setattr(_MODULE, "_git", fake)
    rc = _MODULE._reconcile_worktree({"kept.py"})
    assert rc == 0
    assert not any(c[0] == "restore" for c in fake.calls)
    assert "files were modified" not in capsys.readouterr().err


def test_reconcile_reverts_only_run_induced_changes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Before: developer dirtied feature.py. After: feature.py still
    # dirty + uv.lock newly rewritten by the run.
    after = " M feature.py\n M uv.lock"
    fake = _FakeGit([after])
    monkeypatch.setattr(_MODULE, "_git", fake)
    rc = _MODULE._reconcile_worktree({"feature.py"})
    assert rc == 0
    restore_calls = [c for c in fake.calls if c[0] == "restore"]
    assert restore_calls == [("restore", "--", "uv.lock")]
    err = capsys.readouterr().err
    assert "uv.lock" in err
    assert "feature.py" not in err


def test_reconcile_returns_one_when_restore_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeGit([" M uv.lock"], restore_raises=True)
    monkeypatch.setattr(_MODULE, "_git", fake)
    rc = _MODULE._reconcile_worktree(set())
    assert rc == 1


def test_reconcile_fails_closed_when_post_status_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Post-run `git status` failing means we cannot prove the run left
    # the tree clean: fail closed (return 1) instead of silently
    # passing, so a hidden mutation cannot reach the push.
    fake = _FakeGit([], status_raises=True)
    monkeypatch.setattr(_MODULE, "_git", fake)
    rc = _MODULE._reconcile_worktree(set())
    assert rc == 1
    assert "failing closed" in capsys.readouterr().err


def test_main_skips_reconcile_when_pre_snapshot_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: str, strip: bool = True) -> str:
        msg = "no git"
        raise _MODULE._GitError(msg)

    monkeypatch.setattr(_MODULE, "_git", _raise)
    monkeypatch.setattr(_MODULE, "_run_tests", lambda: 0)
    reconcile_called = False

    def _spy(_before: set[str]) -> int:
        nonlocal reconcile_called
        reconcile_called = True
        return 0

    monkeypatch.setattr(_MODULE, "_reconcile_worktree", _spy)
    assert _MODULE.main() == 0
    assert reconcile_called is False


def test_main_folds_reconcile_failure_into_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "_git", _FakeGit(["", ""]))
    monkeypatch.setattr(_MODULE, "_run_tests", lambda: 0)
    monkeypatch.setattr(_MODULE, "_reconcile_worktree", lambda _b: 1)
    assert _MODULE.main() == 1


def test_main_preserves_test_failure_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_MODULE, "_git", _FakeGit(["", ""]))
    monkeypatch.setattr(_MODULE, "_run_tests", lambda: 1)
    monkeypatch.setattr(_MODULE, "_reconcile_worktree", lambda _b: 0)
    assert _MODULE.main() == 1

"""Unit tests for ``scripts/run_prepush_python_gates.py``.

Covers the worker-count resolution (``_default_jobs`` + the
``PREPUSH_GATE_JOBS`` override), the per-gate output capture
(``_run_gate``), and the serial ``main`` path (pass and failure
reporting). The parallel pool path is exercised by the real pre-push
run rather than a nested ``ProcessPoolExecutor`` under xdist. The script
is loaded as a module so its private helpers are callable.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_prepush_python_gates.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_run_prepush_python_gates",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Loaded once; isolation preserved because every test mutates module
# attributes via ``monkeypatch`` (auto-reverted at teardown).
_MODULE = cast(Any, _load_script_module())  # type: ignore[explicit-any]  # dynamically loaded runner module; attrs resolved by name


# ── _default_jobs ────────────────────────────────────────────────


def test_default_jobs_bounded_by_cap_when_many_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With more cores than the cap, the cap wins."""
    monkeypatch.delenv("PREPUSH_GATE_JOBS", raising=False)
    monkeypatch.setattr(_MODULE.os, "cpu_count", lambda: 64)
    assert _MODULE._default_jobs() == _MODULE._DEFAULT_MAX_JOBS


def test_default_jobs_bounded_by_cores_when_few_cores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With fewer cores than the cap, the core count wins."""
    monkeypatch.delenv("PREPUSH_GATE_JOBS", raising=False)
    monkeypatch.setattr(_MODULE.os, "cpu_count", lambda: 4)
    assert _MODULE._default_jobs() == 4


def test_default_jobs_falls_back_when_cpu_count_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``os.cpu_count()`` returning ``None`` uses the fallback core count."""
    monkeypatch.delenv("PREPUSH_GATE_JOBS", raising=False)
    monkeypatch.setattr(_MODULE.os, "cpu_count", lambda: None)
    expected = min(_MODULE._DEFAULT_MAX_JOBS, _MODULE._FALLBACK_CPU)
    assert _MODULE._default_jobs() == expected


def test_default_jobs_honours_valid_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid positive ``PREPUSH_GATE_JOBS`` overrides the core-based default."""
    monkeypatch.setenv("PREPUSH_GATE_JOBS", "3")
    monkeypatch.setattr(_MODULE.os, "cpu_count", lambda: 64)
    assert _MODULE._default_jobs() == 3


@pytest.mark.parametrize("value", ["0", "-5"])
def test_default_jobs_clamps_non_positive_override(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Zero / negative overrides clamp to a single worker, not error."""
    monkeypatch.setenv("PREPUSH_GATE_JOBS", value)
    assert _MODULE._default_jobs() == 1


@pytest.mark.parametrize("value", ["fast", "1.5", "   "])
def test_default_jobs_warns_and_falls_back_on_malformed_override(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    """A malformed override falls back to the default; non-blank warns loudly."""
    monkeypatch.setenv("PREPUSH_GATE_JOBS", value)
    monkeypatch.setattr(_MODULE.os, "cpu_count", lambda: 4)
    assert _MODULE._default_jobs() == 4
    err = capsys.readouterr().err
    # A blank value is treated as "unset" (no warning); a non-numeric value
    # warns so a typo in the debug lever is not silently ignored.
    if value.strip():
        assert "ignoring PREPUSH_GATE_JOBS" in err
    else:
        assert err == ""


# ── _run_gate ────────────────────────────────────────────────────


def test_run_gate_captures_gate_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate's stdout/stderr are captured into the returned output buffer."""

    def _fake_run_one(stem: str) -> tuple[int, str]:
        # Write through the live sys streams so ``_run_gate``'s redirect
        # captures them, without tripping the no-``print`` lint in tests.
        sys.stdout.write(f"stdout from {stem}\n")
        sys.stderr.write(f"stderr from {stem}\n")
        return 1, "traceback detail"

    monkeypatch.setattr(_MODULE, "_run_one", _fake_run_one)
    stem, code, elapsed, output, detail = _MODULE._run_gate("check_example")
    assert stem == "check_example"
    assert code == 1
    assert elapsed >= 0.0
    assert "stdout from check_example" in output
    assert "stderr from check_example" in output
    assert detail == "traceback detail"


# ── main (serial path) ───────────────────────────────────────────


def test_main_serial_all_pass_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every gate passing under ``--jobs 1`` returns 0 and reports each gate."""
    monkeypatch.setattr(_MODULE, "_GATES", ("gate_a", "gate_b"))
    monkeypatch.setattr(_MODULE, "_run_one", lambda stem: (0, ""))
    rc = _MODULE.main(["--jobs", "1"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "gate_a" in err
    assert "gate_b" in err
    assert "2/2 reported" in err


def test_main_serial_reports_failing_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failing gate under ``--jobs 1`` returns 1 and names the gate + detail."""
    monkeypatch.setattr(_MODULE, "_GATES", ("gate_a", "gate_b"))

    def _fake_run_one(stem: str) -> tuple[int, str]:
        return (1, "why gate_b failed") if stem == "gate_b" else (0, "")

    monkeypatch.setattr(_MODULE, "_run_one", _fake_run_one)
    rc = _MODULE.main(["--jobs", "1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "GATE FAILED: gate_b" in err
    assert "why gate_b failed" in err
    assert "1 failed" in err

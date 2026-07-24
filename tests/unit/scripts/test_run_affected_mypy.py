"""Unit tests for the daemon logic in ``scripts/run_affected_mypy.py``.

Covers the ``scripts/`` daemon scoping rule, the daemon-verdict contract that
decides between trusting dmypy and falling back to a cold run, the RSS reader
behind ``--status``, and the daemon opt-out. Loads the script as a module so
the private helpers are callable.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_affected_mypy.py"


def _load_script_module() -> object:
    """Import the script as a module so private helpers are callable."""
    spec = importlib.util.spec_from_file_location(
        "_run_affected_mypy",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ``_MODULE`` is loaded once at import time and shared across all tests.
# Isolation is preserved because every test mutates module attributes via
# ``monkeypatch`` (auto-reverted at function teardown) rather than touching
# them directly. The script is loaded dynamically so its private helpers have
# no static type; ``cast(Any, ...)`` types the handle once here instead of an
# ``# type: ignore[attr-defined]`` on every access site.
_MODULE = cast(Any, _load_script_module())  # type: ignore[explicit-any]  # dynamically loaded hook module; attrs resolved by name


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        pytest.param(["src/synthorg/workers/worker.py"], False, id="leaf-src-module"),
        pytest.param(
            ["tests/unit/workers/test_worker.py"], False, id="leaf-test-module"
        ),
        pytest.param([], False, id="nothing-changed"),
        pytest.param(["scripts/check_no_stubs.py"], True, id="a-script"),
        pytest.param(["src/synthorg/core/types.py"], True, id="foundational-core"),
        pytest.param(["src/synthorg/config/loader.py"], True, id="foundational-config"),
        pytest.param(["tests/unit/conftest.py"], True, id="a-conftest"),
    ],
)
def test_scripts_scope_rule(changed: list[str], expected: bool) -> None:
    """Only changes that can reach ``scripts/`` pull in its daemon."""
    assert _MODULE._scripts_in_scope(changed) is expected


def test_scripts_scope_includes_everything_when_git_is_unreadable() -> None:
    """An unknown diff resolves toward checking more, never less."""
    assert _MODULE._scripts_in_scope(None) is True


@pytest.mark.parametrize("code", [0, 1])
def test_check_daemon_trusts_mypy_result_codes(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """0 (clean) and 1 (type errors) are verdicts and pass straight through."""
    monkeypatch.setattr(_MODULE, "_dmypy", lambda *a, **k: code)
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) == code


@pytest.mark.parametrize("code", [2, 130, -1])
def test_check_daemon_reports_no_verdict_on_daemon_failure(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """A busy or broken daemon yields no verdict, so the caller re-checks cold."""
    monkeypatch.setattr(_MODULE, "_dmypy", lambda *a, **k: code)
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) is None


def test_daemon_pass_skips_cold_scripts_daemon_when_out_of_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A leaf change must not spend ~1.5GB starting the scripts daemon."""
    checked: list[str] = []

    def _fake_check(daemon: Any) -> int:  # type: ignore[explicit-any]  # module handle is untyped
        checked.append(daemon.label)
        return 0

    monkeypatch.setattr(_MODULE, "_check_daemon", _fake_check)
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: False)

    assert _MODULE._run_daemon_pass(["src/synthorg/workers/worker.py"]) == 0
    assert checked == ["main"]


def test_daemon_pass_uses_warm_scripts_daemon_even_when_out_of_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once it is already resident the extra coverage costs almost nothing."""
    checked: list[str] = []

    def _fake_check(daemon: Any) -> int:  # type: ignore[explicit-any]  # module handle is untyped
        checked.append(daemon.label)
        return 0

    monkeypatch.setattr(_MODULE, "_check_daemon", _fake_check)
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: True)

    assert _MODULE._run_daemon_pass(["src/synthorg/workers/worker.py"]) == 0
    assert checked == ["main", "scripts"]


def test_daemon_pass_surfaces_worst_code_across_both_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Type errors in ``scripts/`` alone must still fail the push."""
    monkeypatch.setattr(
        _MODULE,
        "_check_daemon",
        lambda daemon: 0 if daemon.label == "main" else 1,
    )
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: True)

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) == 1


def test_daemon_pass_falls_back_when_main_daemon_gives_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed main daemon must never be reported as a clean tree."""
    monkeypatch.setattr(_MODULE, "_check_daemon", lambda _daemon: None)
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: False)

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) is None


def test_daemon_pass_falls_back_when_scripts_daemon_gives_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean main scope does not excuse an unanswered scripts scope."""
    monkeypatch.setattr(
        _MODULE,
        "_check_daemon",
        lambda daemon: 0 if daemon.label == "main" else None,
    )
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: True)

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) is None


def test_daemons_do_not_share_a_status_file() -> None:
    """Sharing one would restart the daemon on every alternating invocation."""
    status_files = {daemon.status_file for daemon in _MODULE._ALL_DAEMONS}
    assert len(status_files) == len(_MODULE._ALL_DAEMONS)


def test_scripts_daemon_suppresses_unused_config_warnings() -> None:
    """Without this the daemon exits 1 on a clean tree and fails every push."""
    assert "--no-warn-unused-configs" in _MODULE._SCRIPTS_DAEMON.extra


def test_scripts_cold_and_daemon_paths_share_one_flag_set() -> None:
    """Divergent flags would let the two paths disagree on the same tree."""
    assert _MODULE._SCRIPTS_DAEMON.extra == (
        "--explicit-package-bases",
        "--no-warn-unused-configs",
    )


@pytest.mark.parametrize(
    ("env_var", "value"),
    [("SYNTHORG_NO_DMYPY", "1"), ("CI", "true")],
)
def test_daemon_opted_out(
    monkeypatch: pytest.MonkeyPatch, env_var: str, value: str
) -> None:
    """Both the explicit opt-out and CI bypass the daemon."""
    monkeypatch.delenv("SYNTHORG_NO_DMYPY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(env_var, value)
    assert _MODULE._daemon_opted_out() is True


def test_daemon_not_opted_out_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain local push uses the daemon."""
    monkeypatch.delenv("SYNTHORG_NO_DMYPY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert _MODULE._daemon_opted_out() is False


def _tasklist_row(memory_field: str) -> str:
    """Build a ``tasklist /FO CSV /NH`` row carrying *memory_field*.

    The thousands separator is locale-dependent and can be the CSV delimiter
    itself, which is the case this row exists to pin down.
    """
    return f'"python.exe","1","Console","1","{memory_field}"\n'


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        pytest.param(_tasklist_row("2,556,996 K"), 2497, id="comma"),
        pytest.param(_tasklist_row("2.556.996 K"), 2497, id="dot"),
        pytest.param(_tasklist_row("1 048 576 K"), 1024, id="space"),
        # Escaped rather than literal: this repo bans ambiguous unicode in
        # source, and it is the separator this machine's locale actually emits.
        pytest.param(_tasklist_row("2\u2019556\u2019996 K"), 2497, id="apostrophe"),
    ],
)
def test_process_rss_parses_windows_thousands_separators(
    monkeypatch: pytest.MonkeyPatch, stdout: str, expected: int
) -> None:
    """``tasklist`` separates thousands per locale, so digits are all that count."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        _MODULE.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout, ""),
    )
    assert _MODULE._process_rss_mb(1) == expected


def test_process_rss_parses_posix_kilobytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ps -o rss=`` reports bare kilobytes."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        _MODULE.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "  2097152\n", ""),
    )
    assert _MODULE._process_rss_mb(1) == 2048


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    [(1, ""), (0, ""), (0, "   \n")],
)
def test_process_rss_returns_none_when_unreadable(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str
) -> None:
    """A dead or unreadable process reports no size rather than a wrong one."""
    monkeypatch.setattr(
        _MODULE.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, ""),
    )
    assert _MODULE._process_rss_mb(1) is None


def test_process_rss_survives_a_missing_reporting_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--status`` must not crash where ``tasklist``/``ps`` is absent."""

    message = "no such binary"

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise OSError(message)

    monkeypatch.setattr(_MODULE.subprocess, "run", _raise)
    assert _MODULE._process_rss_mb(1) is None


def test_daemon_pid_returns_none_for_a_missing_status_file(tmp_path: Path) -> None:
    """A stopped daemon leaves no status file behind."""
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=tmp_path / "absent.json")
    assert _MODULE._daemon_pid(daemon) is None


def test_daemon_pid_returns_none_for_a_corrupt_status_file(tmp_path: Path) -> None:
    """A half-written status file must not crash ``--status`` or ``--stop``."""
    status_file = tmp_path / "corrupt.json"
    status_file.write_text("{not json", encoding="utf-8")
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=status_file)
    assert _MODULE._daemon_pid(daemon) is None


def test_daemon_pid_reads_the_recorded_pid(tmp_path: Path) -> None:
    """The pid is what lets ``--status`` report resident memory."""
    status_file = tmp_path / "status.json"
    status_file.write_text('{"pid": 4242, "connection_name": "x"}', encoding="utf-8")
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=status_file)
    assert _MODULE._daemon_pid(daemon) == 4242

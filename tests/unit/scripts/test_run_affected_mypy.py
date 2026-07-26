"""Unit tests for the daemon logic in ``scripts/run_affected_mypy.py``.

Covers the ``scripts/`` daemon scoping rule, the daemon-verdict contract that
decides between trusting dmypy and falling back to a cold run, the RSS reader
behind ``--status``, and the daemon opt-out. Loads the script as a module so
the private helpers are callable.
"""

import argparse
import importlib.util
import inspect
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


# Captured before the autouse fixture can replace them, so the tests that
# exercise the bookkeeping itself reach the real implementations rather than
# the stubs every other test needs.
_REAL_ADOPT_IDLE_TIMEOUT = _MODULE._adopt_idle_timeout
_REAL_RECORD_BOUNDED_LIFETIME = _MODULE._record_bounded_lifetime
_REAL_FORGET_BOUNDED_LIFETIME = _MODULE._forget_bounded_lifetime
# Captured for the same reason: the ``main`` tests replace ``_parse_args``,
# and the helper that builds their arguments must not re-enter the stub.
_REAL_PARSE_ARGS = _MODULE._parse_args


@pytest.fixture(autouse=True)
def _stub_daemon_lifetime_bookkeeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the daemon lifetime bookkeeping off the real machine.

    Most tests here drive ``_check_daemon`` or ``_stop`` against the real
    ``_MAIN_DAEMON`` / ``_ALL_DAEMONS`` (they have to: the point is asserting
    the real scope is threaded through), stubbing only the ``dmypy`` call
    itself. The bookkeeping added around that call reaches real system state
    by three separate routes -- adoption can issue a genuine ``dmypy stop``,
    the marker write lands beside the status file in the repo root, and
    ``_stop`` unlinks that marker -- none of which the ``dmypy`` stub covers.

    Left unstubbed, a unit run deletes the developer's lifetime markers, so
    the next invocation sees an unvouched daemon and restarts it: a two-minute
    graph rebuild charged to the next push, which is what breaches the push
    budget. All three are stubbed together because they are one concern, and
    autouse rather than opt-in because the damage lands on a later push
    instead of failing anything here, so an opt-in a test forgets is silent.
    """
    monkeypatch.setattr(_MODULE, "_adopt_idle_timeout", lambda _daemon: None)
    monkeypatch.setattr(_MODULE, "_record_bounded_lifetime", lambda _daemon: None)
    monkeypatch.setattr(_MODULE, "_forget_bounded_lifetime", lambda _daemon: None)


def _isolated_daemon(tmp_path: Path) -> Any:  # type: ignore[explicit-any]
    """A daemon whose status and lifetime files live under *tmp_path*."""
    return _MODULE._MAIN_DAEMON._replace(status_file=tmp_path / ".dmypy-main.json")


def _write_status(daemon: Any, pid: int) -> None:  # type: ignore[explicit-any]
    """Write the dmypy status file *daemon* reads its pid from."""
    daemon.status_file.write_text(f'{{"pid": {pid}}}', encoding="utf-8")


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
    """A persistently broken daemon yields no verdict, so the caller goes cold."""
    monkeypatch.setattr(_MODULE, "_dmypy", lambda *a, **k: code)
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) is None


def test_check_daemon_retries_past_a_stale_status_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead daemon fails once then serves the replacement it just started."""
    codes = iter([2, 0])
    monkeypatch.setattr(_MODULE, "_dmypy", lambda *a, **k: next(codes))
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) == 0


def test_check_daemon_retry_preserves_a_real_type_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying must not mask type errors found on the second attempt."""
    codes = iter([2, 1])
    monkeypatch.setattr(_MODULE, "_dmypy", lambda *a, **k: next(codes))
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) == 1


def test_check_daemon_does_not_retry_a_clean_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict on the first attempt must not cost a second full check."""
    attempts = 0

    def _count(*_args: object, **_kwargs: object) -> int:
        nonlocal attempts
        attempts += 1
        return 0

    monkeypatch.setattr(_MODULE, "_dmypy", _count)
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) == 0
    assert attempts == 1


def test_check_daemon_gives_up_after_the_bounded_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback must not be delayed indefinitely by a broken daemon."""
    attempts = 0

    def _count(*_args: object, **_kwargs: object) -> int:
        nonlocal attempts
        attempts += 1
        return 2

    monkeypatch.setattr(_MODULE, "_dmypy", _count)
    assert _MODULE._check_daemon(_MODULE._MAIN_DAEMON) is None
    assert attempts == _MODULE._DAEMON_ATTEMPTS


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
        lambda daemon: 0 if daemon.label == _MODULE._MAIN_DAEMON.label else 1,
    )

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) == 1


def test_daemon_pass_falls_back_when_main_daemon_gives_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed main daemon must never be reported as a clean tree."""
    monkeypatch.setattr(_MODULE, "_check_daemon", lambda _daemon: None)

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) is None


def test_daemon_pass_falls_back_when_scripts_daemon_gives_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean main scope does not excuse an unanswered scripts scope."""
    monkeypatch.setattr(
        _MODULE,
        "_check_daemon",
        lambda daemon: 0 if daemon.label == _MODULE._MAIN_DAEMON.label else None,
    )

    assert _MODULE._run_daemon_pass(["scripts/x.py"]) is None


def test_check_daemon_threads_the_daemon_scope_into_the_dmypy_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped path or flag would silently narrow what the gate checks."""
    calls: list[tuple[object, ...]] = []

    def _record(daemon: object, *args: str, **_kwargs: object) -> int:
        calls.append((daemon, *args))
        return 0

    monkeypatch.setattr(_MODULE, "_dmypy", _record)
    _MODULE._check_daemon(_MODULE._SCRIPTS_DAEMON)

    assert calls == [
        (
            _MODULE._SCRIPTS_DAEMON,
            "run",
            "--timeout",
            str(_MODULE._DAEMON_IDLE_TIMEOUT_SECONDS),
            "--",
            *_MODULE._SCRIPTS_DAEMON.paths,
            *_MODULE._SCRIPTS_DAEMON.extra,
        )
    ]


def test_scripts_cold_and_daemon_paths_pass_identical_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Divergent flags would let the two paths disagree on the same tree."""
    daemon_extra: list[str] = []
    cold_extra: list[str] = []

    def _record_daemon(_daemon: object, *args: str, **_kwargs: object) -> int:
        daemon_extra.extend(args)
        return 0

    def _record_cold(
        paths: list[str], *, env: object = None, extra: list[str] | None = None
    ) -> int:
        cold_extra.extend(paths)
        cold_extra.extend(extra or [])
        return 0

    monkeypatch.setattr(_MODULE, "_dmypy", _record_daemon)
    monkeypatch.setattr(_MODULE, "_invoke_mypy", _record_cold)

    _MODULE._check_daemon(_MODULE._SCRIPTS_DAEMON)
    _MODULE._run_scripts_mypy()

    # The daemon call carries a "run <daemon-management flags> --" preamble the
    # cold call does not. Split on the separator rather than a fixed offset: the
    # flags that must match are exactly the ones mypy itself sees, and anchoring
    # on a length lets a new pre-separator flag shift the slice so this asserts
    # parity on the wrong span instead of failing.
    assert daemon_extra[0] == "run"
    separator = daemon_extra.index("--")
    assert daemon_extra[separator + 1 :] == cold_extra


def test_every_daemon_starts_with_a_bounded_idle_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbounded daemon outlives its session and holds the worktree open."""
    seen: list[tuple[str, ...]] = []

    def _record(_daemon: object, *args: str, **_kwargs: object) -> int:
        seen.append(args)
        return 0

    monkeypatch.setattr(_MODULE, "_dmypy", _record)
    for daemon in _MODULE._ALL_DAEMONS:
        _MODULE._check_daemon(daemon)

    assert len(seen) == len(_MODULE._ALL_DAEMONS)
    for args in seen:
        # Before the separator, so dmypy consumes it as a daemon-management
        # flag rather than forwarding it to mypy as a checked path.
        separator = args.index("--")
        timeout_flag = args.index("--timeout")
        assert timeout_flag < separator
        assert int(args[timeout_flag + 1]) == _MODULE._DAEMON_IDLE_TIMEOUT_SECONDS


class TestIdleTimeoutAdoption:
    """A daemon started before the bound existed still has to expire.

    dmypy fixes the idle lifetime when the process starts, so passing
    ``--timeout`` on every ``run`` binds new daemons only. Without adoption
    the guarantee would skip precisely the long-lived daemons it exists for:
    the one already warm on a developer's machine when this landed, and any
    started by a bare ``dmypy run``.
    """

    def test_marker_sits_beside_the_status_file(self, tmp_path: Path) -> None:
        daemon = _isolated_daemon(tmp_path)
        assert daemon.lifetime_file == tmp_path / ".dmypy-main.lifetime.json"

    def test_a_daemon_this_script_started_is_left_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daemon = _isolated_daemon(tmp_path)
        _write_status(daemon, 4242)
        _REAL_RECORD_BOUNDED_LIFETIME(daemon)
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            _MODULE,
            "_dmypy_result",
            lambda _d, *args, **_kw: calls.append(args),
        )

        _REAL_ADOPT_IDLE_TIMEOUT(daemon)

        assert calls == []
        assert daemon.lifetime_file.exists()

    def test_a_pre_existing_daemon_is_stopped_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A marker naming a DIFFERENT pid is the shape a daemon someone else
        # started leaves behind: it vouches for nothing about the process now
        # listening, so that process is unbounded and holds the worktree open.
        daemon = _isolated_daemon(tmp_path)
        _write_status(daemon, 4242)
        _REAL_RECORD_BOUNDED_LIFETIME(daemon)
        _write_status(daemon, 5151)
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: True)
        monkeypatch.setattr(
            _MODULE,
            "_dmypy_result",
            lambda _d, *args, **_kw: calls.append(args),
        )
        monkeypatch.setattr(
            _MODULE, "_forget_bounded_lifetime", _REAL_FORGET_BOUNDED_LIFETIME
        )

        _REAL_ADOPT_IDLE_TIMEOUT(daemon)

        assert calls == [("stop",)]
        # The stale marker must go with it, or a recycled pid could later
        # match it and vouch for a daemon nothing verified.
        assert not daemon.lifetime_file.exists()

    def test_a_changed_bound_rebinds_a_warm_daemon(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Editing the constant must reach daemons already running under the
        # old one, else the value in the source stops describing the machine.
        daemon = _isolated_daemon(tmp_path)
        _write_status(daemon, 4242)
        _REAL_RECORD_BOUNDED_LIFETIME(daemon)
        monkeypatch.setattr(_MODULE, "_DAEMON_IDLE_TIMEOUT_SECONDS", 60)

        assert _MODULE._recorded_lifetime_pid(daemon) is None

    def test_a_recycled_pid_does_not_inherit_the_marker(self, tmp_path: Path) -> None:
        daemon = _isolated_daemon(tmp_path)
        _write_status(daemon, 4242)
        _REAL_RECORD_BOUNDED_LIFETIME(daemon)
        _write_status(daemon, 5151)

        assert _MODULE._recorded_lifetime_pid(daemon) != _MODULE._daemon_pid(daemon)

    @pytest.mark.parametrize(
        "marker",
        [
            pytest.param("not json at all", id="unparseable"),
            pytest.param("[1, 2]", id="not-an-object"),
            pytest.param('{"idle_timeout_seconds": 7200}', id="no-pid"),
            pytest.param('{"pid": true, "idle_timeout_seconds": 7200}', id="bool-pid"),
        ],
    )
    def test_an_unusable_marker_reads_as_unbounded(
        self, tmp_path: Path, marker: str
    ) -> None:
        # Failing open here would vouch for a daemon nothing verified.
        daemon = _isolated_daemon(tmp_path)
        daemon.lifetime_file.write_text(marker, encoding="utf-8")

        assert _MODULE._recorded_lifetime_pid(daemon) is None

    def test_a_cold_scope_is_not_restarted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daemon = _isolated_daemon(tmp_path)
        calls: list[tuple[str, ...]] = []
        monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: False)
        monkeypatch.setattr(
            _MODULE,
            "_dmypy_result",
            lambda _d, *args, **_kw: calls.append(args),
        )

        _REAL_ADOPT_IDLE_TIMEOUT(daemon)

        assert calls == []


class TestWorktreeHolders:
    """Finding what holds a worktree open, without offering up a neighbour.

    This replaces a PowerShell snippet that lived in two skill docs. Keeping
    the matching in Python is the point: it is the rule that decides what an
    operator is invited to kill, so it belongs somewhere testable rather than
    in a quoting-sensitive shell predicate duplicated across files.
    """

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            pytest.param(r"python.exe C:\wt\foo", True, id="at-end"),
            pytest.param(r"python.exe C:\wt\foo\.venv\python.exe", True, id="nested"),
            pytest.param(r'python.exe "C:\wt\foo" --x', True, id="quoted"),
            pytest.param(r"python.exe C:\wt\foo --x", True, id="argument-break"),
            # The dangerous one: a sibling whose name extends this one.
            pytest.param(r"python.exe C:\wt\foo2\.venv\python.exe", False, id="prefix"),
            pytest.param(r"python.exe C:\wt\bar", False, id="unrelated"),
        ],
    )
    def test_path_matching_respects_boundaries(
        self, command: str, expected: bool
    ) -> None:
        assert _MODULE._references_path(command, r"C:\wt\foo") is expected

    def test_posix_process_table_parsed(self) -> None:
        output = "  123 /usr/bin/python -m mypy.dmypy\n  456 sleep 1\nnot-a-row\n"

        assert list(_MODULE._parse_posix_process_table(output)) == [
            (123, "/usr/bin/python -m mypy.dmypy"),
            (456, "sleep 1"),
        ]

    def test_windows_process_table_parsed(self) -> None:
        # ConvertTo-Csv emits a header row, and a command line containing a
        # comma must survive as one field rather than splitting the row.
        output = (
            '"ProcessId","CommandLine"\n'
            '"123","python.exe -m mypy.dmypy --opts=a,b"\n'
            '"456",\n'
        )

        assert list(_MODULE._parse_windows_process_table(output)) == [
            (123, "python.exe -m mypy.dmypy --opts=a,b"),
            (456, ""),
        ]

    def test_a_missing_path_is_a_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _MODULE._find_holders(str(tmp_path / "absent")) == 2
        assert "does not exist" in capsys.readouterr().err

    def test_listing_never_terminates_anything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The read-only half must stay read-only: the whole design rests on an
        # operator seeing the list before anything is killed.
        monkeypatch.setattr(
            _MODULE, "_process_table", lambda: [(1, f"python {tmp_path}")]
        )
        killed: list[int] = []
        monkeypatch.setattr(_MODULE, "_stop_holder", killed.append)

        assert _MODULE._find_holders(str(tmp_path)) == 0
        assert killed == []


def test_daemons_do_not_share_a_status_file() -> None:
    """Sharing one would restart the daemon on every alternating invocation."""
    status_files = {daemon.status_file for daemon in _MODULE._ALL_DAEMONS}
    assert len(status_files) == len(_MODULE._ALL_DAEMONS)


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param(["tests/e2e/test_flow.py"], id="e2e"),
        pytest.param(["tests/conformance/persistence/test_x.py"], id="conformance"),
        pytest.param(["tests/benchmarks/test_perf.py"], id="benchmarks"),
        pytest.param(["tests/evals/test_eval.py"], id="evals"),
        pytest.param(["tests/baselines/helper.py"], id="baselines"),
        pytest.param(["tests/shallow.py"], id="shallow-tests-file"),
    ],
)
def test_unmapped_test_directories_defer_instead_of_vanishing(
    changed: list[str],
) -> None:
    """A tests/ kind with no narrow mapping must be handed to CI.

    Classifying it "other" would drop it silently: the gate would exit 0 on
    an unexamined change with nothing announced. The second return value is
    the deferral flag, so it must be ``True`` for these kinds.
    """
    _paths, deferred = _MODULE._affected_mypy_paths(changed)
    assert deferred is True


@pytest.mark.parametrize(
    ("changed", "required"),
    [
        pytest.param(
            ["src/synthorg/workers/worker.py"],
            {"src/synthorg/workers", "tests/unit/workers"},
            id="src-module-pulls-its-tests",
        ),
        pytest.param(
            ["tests/unit/workers/test_worker.py"],
            {"tests/unit/workers"},
            id="test-module-alone",
        ),
    ],
)
def test_affected_paths_map_a_change_to_real_targets(
    changed: list[str], required: set[str]
) -> None:
    """The cold path's target list is what actually gets type-checked.

    Asserts the required targets are present rather than an exact list: a
    module may also own integration tests, and gaining one should not fail
    this.
    """
    paths, run_all = _MODULE._affected_mypy_paths(changed)
    assert run_all is False
    assert required <= set(paths)
    assert all((_REPO_ROOT / path).exists() for path in paths)


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


@pytest.mark.parametrize("value", ["", "0", "false", "FALSE", "no"])
def test_falsey_env_values_do_not_opt_out(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``CI=false`` means not in CI, so it must not disable the daemon."""
    monkeypatch.delenv("SYNTHORG_NO_DMYPY", raising=False)
    monkeypatch.setenv("CI", value)
    assert _MODULE._daemon_opted_out() is False


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


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not json", id="not-json"),
        pytest.param("null", id="valid-json-null"),
        pytest.param("[]", id="valid-json-list"),
        pytest.param('"a string"', id="valid-json-scalar"),
        pytest.param("42", id="valid-json-number"),
    ],
)
def test_daemon_pid_returns_none_for_an_unusable_status_file(
    tmp_path: Path, content: str
) -> None:
    """Neither malformed JSON nor valid non-object JSON may crash the CLI.

    Valid-but-not-an-object is the sharper case: it parses cleanly, so only an
    explicit shape check stops ``.get`` raising AttributeError.
    """
    status_file = tmp_path / "unusable.json"
    status_file.write_text(content, encoding="utf-8")
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=status_file)
    assert _MODULE._daemon_pid(daemon) is None


def test_daemon_pid_rejects_a_boolean_pid(tmp_path: Path) -> None:
    """``bool`` subclasses ``int``, and a pid of ``True`` is not a pid."""
    status_file = tmp_path / "bool.json"
    status_file.write_text('{"pid": true}', encoding="utf-8")
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=status_file)
    assert _MODULE._daemon_pid(daemon) is None


def test_daemon_pid_reads_the_recorded_pid(tmp_path: Path) -> None:
    """The pid is what lets ``--status`` report resident memory."""
    status_file = tmp_path / "status.json"
    status_file.write_text('{"pid": 4242, "connection_name": "x"}', encoding="utf-8")
    daemon = _MODULE._MAIN_DAEMON._replace(status_file=status_file)
    assert _MODULE._daemon_pid(daemon) == 4242


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> object:
    """Build a dmypy CompletedProcess stand-in."""
    return subprocess.CompletedProcess(["dmypy"], returncode, stdout, stderr)


def test_stop_reports_failure_when_a_daemon_refuses_to_stop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A busy daemon must not be reported as stopped, nor exit 0.

    dmypy uses one exit code for "not running" and "running but wedged", so
    only the message text separates them.
    """
    monkeypatch.setattr(
        _MODULE,
        "_dmypy_result",
        lambda *_a, **_k: _completed(2, stderr="Daemon may be busy processing"),
    )
    monkeypatch.setattr(_MODULE, "_daemon_pid", lambda _daemon: None)

    assert _MODULE._stop() == 1
    assert "stop FAILED" in capsys.readouterr().err


def test_stop_reports_success_when_no_daemon_is_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing to stop is a clean outcome, not a failure."""
    monkeypatch.setattr(
        _MODULE,
        "_dmypy_result",
        lambda *_a, **_k: _completed(2, stderr="No status file found"),
    )
    monkeypatch.setattr(_MODULE, "_daemon_pid", lambda _daemon: None)

    assert _MODULE._stop() == 0
    assert "not running" in capsys.readouterr().out


def test_management_subcommands_do_not_inherit_the_build_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stop`` and ``status`` must use the short ceiling, not the build one.

    ``stop`` exists to reclaim memory before a heavy build, so a wedged daemon
    that made it block for the full build timeout would defeat the reason for
    calling it. The sentinel default means a call site that passes no timeout
    at all fails here rather than silently getting the build ceiling.
    """
    seen: list[tuple[str, int]] = []

    def _record(
        _daemon: object, *args: str, quiet: bool = False, timeout: int = -1
    ) -> object:
        seen.append((args[0], timeout))
        return _completed(0)

    monkeypatch.setattr(_MODULE, "_dmypy_result", _record)
    monkeypatch.setattr(_MODULE, "_daemon_pid", lambda _daemon: None)

    _MODULE._stop()
    _MODULE._daemon_running(_MODULE._MAIN_DAEMON)

    assert {subcommand for subcommand, _ in seen} == {"stop", "status"}
    assert all(timeout == _MODULE._PROCESS_QUERY_TIMEOUT_SECONDS for _, timeout in seen)


def test_a_run_without_an_explicit_timeout_keeps_the_build_ceiling() -> None:
    """The full-tree ``run`` legitimately takes minutes, so it keeps 1800s."""
    default = inspect.signature(_MODULE._dmypy_result).parameters["timeout"].default
    assert default == _MODULE._MYPY_TIMEOUT_SECONDS


def test_main_skips_everything_when_no_python_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A docs-only push must not wake the daemon or pay a cold build."""
    monkeypatch.setattr(_MODULE, "_parse_args", _no_flags)
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", list)
    monkeypatch.setattr(_MODULE, "_run_daemon_pass", _unreachable)
    monkeypatch.setattr(_MODULE, "_run_full", _unreachable)

    assert _MODULE.main() == 0


def test_main_defers_a_pyproject_only_change_instead_of_skipping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config-only push alters how mypy runs, so it is announced not dropped.

    ``pyproject.toml`` carries mypy's own config and the dependency pins with
    no ``.py`` in the diff; reporting "No Python files changed" would hide a
    whole-tree question from the reader.
    """
    monkeypatch.setattr(_MODULE, "_parse_args", _no_flags)
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", lambda: ["pyproject.toml"])
    monkeypatch.setattr(_MODULE, "_run_daemon_pass", _unreachable)
    monkeypatch.setattr(_MODULE, "_run_full", _unreachable)

    assert _MODULE.main() == 0
    out = capsys.readouterr().out
    assert "deferred to CI" in out
    assert "No Python files changed" not in out


def test_main_returns_the_daemon_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon verdict is authoritative and short-circuits the cold path."""
    monkeypatch.setattr(_MODULE, "_parse_args", _no_flags)
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", lambda: ["src/x.py"])
    monkeypatch.setattr(_MODULE, "_daemon_opted_out", lambda: False)
    monkeypatch.setattr(_MODULE, "_run_daemon_pass", lambda _changed: 1)
    monkeypatch.setattr(_MODULE, "_run_full", _unreachable)

    assert _MODULE.main() == 1


def test_main_runs_full_cold_when_the_diff_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown diff must widen to the full tree, never skip."""
    monkeypatch.setattr(_MODULE, "_parse_args", _no_flags)
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", lambda: None)
    monkeypatch.setattr(_MODULE, "_daemon_opted_out", lambda: True)
    monkeypatch.setattr(_MODULE, "_run_full", lambda: 0)

    assert _MODULE.main() == 0


def test_main_falls_back_cold_when_the_daemon_gives_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate must re-derive an answer rather than report the daemon's silence."""
    monkeypatch.setattr(_MODULE, "_parse_args", _no_flags)
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", lambda: None)
    monkeypatch.setattr(_MODULE, "_daemon_opted_out", lambda: False)
    monkeypatch.setattr(_MODULE, "_run_daemon_pass", lambda _changed: None)
    monkeypatch.setattr(_MODULE, "_run_full", lambda: 1)

    assert _MODULE.main() == 1


@pytest.mark.parametrize("flag", ["warm", "stop", "status"])
def test_main_dispatches_each_management_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """Each subcommand runs instead of, never alongside, a type check."""
    monkeypatch.setattr(_MODULE, "_parse_args", lambda: _flag_args(**{flag: True}))
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", _unreachable)
    monkeypatch.setattr(_MODULE, f"_{flag}", lambda: 0)

    assert _MODULE.main() == 0


def test_full_runs_the_cold_ci_scope_without_a_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--full`` is the only way to ask for the cold whole-tree scope.

    The daemon always checks the full scope but never cold, and the cold
    path defers the whole-tree question to CI, so neither reproduces what
    CI's Type Check job runs.
    """
    monkeypatch.setattr(_MODULE, "_parse_args", lambda: _flag_args(full=True))
    monkeypatch.setattr(_MODULE, "_resolve_changed_files", _unreachable)
    monkeypatch.setattr(_MODULE, "_run_daemon_pass", _unreachable)
    monkeypatch.setattr(_MODULE, "_run_full", lambda: 0)

    assert _MODULE.main() == 0


def _no_flags() -> argparse.Namespace:
    """Return parsed args for a plain pre-push invocation.

    Parsed from the real parser with an empty argv rather than hand-listed:
    an enumerated ``Namespace`` goes stale the moment a flag is added, and
    fails as an ``AttributeError`` inside ``main`` that says nothing about
    the actual cause.
    """
    argv = sys.argv
    try:
        sys.argv = [_SCRIPT_PATH.name]
        return cast("argparse.Namespace", _REAL_PARSE_ARGS())
    finally:
        sys.argv = argv


def _flag_args(**overrides: bool) -> argparse.Namespace:
    """Return parsed args with the named management flags turned on."""
    args = _no_flags()
    for name, value in overrides.items():
        setattr(args, name, value)
    return args


def _unreachable(*_args: object, **_kwargs: object) -> int:
    """Fail loudly if a path the test forbids is taken anyway."""
    message = "this code path should not have been reached"
    raise AssertionError(message)

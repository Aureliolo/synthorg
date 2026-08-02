"""Tests for the ways a mypy daemon costs a push without being wrong.

Every failure here shares a shape: something still answers, so nothing
reports a problem, and the cost lands inside whatever ran next.

* A ``uv sync`` invalidates the resident graph without stopping the
  daemon. dmypy discovers that only once a check is underway and pays a
  124s cold rebuild inside it, against 1.4s warm.
* A wedged server outlives the client that timed out against it, so the
  bounded retry walks back into the same wedge for another full build
  ceiling rather than getting a fresh daemon.
* A status file holds one pid. When a second server starts for the same
  file the first keeps its graph and loses every way of being reached,
  while the winner holds nothing and reports "running": measured once at
  a 36MB daemon called warm next to a 2577MB one nothing referenced, and
  the check that followed rebuilt for 140s.

The fingerprint only catches the staleness it can see, so one group
covers the rest: a rebuild that happens anyway is at least named, because
a 162s check and a 2s check look identical from outside.

Lives apart from ``test_run_affected_mypy.py`` because that module is
already well past the tests size budget.
"""

import importlib.util
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    """Load the hook script by path.

    Returns:
        The module. ``ModuleType.__getattr__`` is already typed ``Any``,
        so attribute access resolves without an explicit-Any opt-out.
    """
    script = _REPO_ROOT / "scripts" / "run_affected_mypy.py"
    spec = importlib.util.spec_from_file_location("_run_affected_mypy_state", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load()


def _daemon(tmp_path: Path) -> Any:  # type: ignore[explicit-any]
    return _MODULE._MAIN_DAEMON._replace(status_file=tmp_path / ".dmypy-main.json")


def _running(daemon: Any, pid: int = 4242) -> None:  # type: ignore[explicit-any]
    daemon.status_file.write_text(f'{{"pid": {pid}}}', encoding="utf-8")


def _record_marker(daemon: Any, digest: str | None) -> None:  # type: ignore[explicit-any]
    """Write the lifetime marker as ``_record_bounded_lifetime`` would."""
    payload: dict[str, object] = {
        "pid": 4242,
        "idle_timeout_seconds": _MODULE._DAEMON_IDLE_TIMEOUT_SECONDS,
    }
    if digest is not None:
        payload["dependency_digest"] = digest
    daemon.lifetime_file.write_text(json.dumps(payload), encoding="utf-8")


# Captured before the autouse fixture replaces it, so the reaper's own test
# reaches the real implementation.
_REAL_REAP = _MODULE._reap_orphaned_servers


@pytest.fixture(autouse=True)
def _keep_the_machine_out_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Stop the daemon lifecycle reaching real processes.

    ``_check_daemon`` enumerates processes to reap orphans and can wait out
    a grace period for a starting server. Both are real system calls against
    the developer's own daemons, and the wait would sleep past the suite's
    timeout in any test where no status file ever appears.

    The per-run process-table snapshot is cleared around every test: it is
    memoised for the life of the process, so one test's table would
    otherwise answer the next one's question.
    """
    _MODULE._process_table_snapshot.cache_clear()
    monkeypatch.setattr(_MODULE, "_reap_orphaned_servers", lambda _daemon, **_kw: None)
    monkeypatch.setattr(_MODULE, "_wait_for_daemon", lambda _daemon: False)
    yield
    _MODULE._process_table_snapshot.cache_clear()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every dmypy subcommand instead of running one."""
    recorded: list[tuple[str, ...]] = []

    def _fake(daemon: object, *args: str, **_kwargs: object) -> object:
        recorded.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_MODULE, "_dmypy_result", _fake)
    monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: True)
    return recorded


class TestStaleGraphDetection:
    """A graph built against different dependencies must not be trusted."""

    def test_a_changed_lockfile_stops_the_daemon(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        daemon = _daemon(tmp_path)
        _running(daemon)
        _record_marker(daemon, "digest-at-start")
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: "digest-now")
        _MODULE._drop_stale_graph(daemon)
        assert ("stop",) in calls

    def test_an_unchanged_lockfile_leaves_it_alone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        # The warm daemon is the whole point; restarting a valid one would
        # charge every push the rebuild this exists to avoid.
        daemon = _daemon(tmp_path)
        _running(daemon)
        _record_marker(daemon, "same-digest")
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: "same-digest")
        _MODULE._drop_stale_graph(daemon)
        assert calls == []

    def test_a_daemon_with_no_recorded_digest_is_left_alone(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        # An unvouched daemon belongs to _adopt_idle_timeout; claiming it
        # here would restart it twice for two different reasons.
        daemon = _daemon(tmp_path)
        _running(daemon)
        _record_marker(daemon, None)
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: "digest-now")
        _MODULE._drop_stale_graph(daemon)
        assert calls == []

    def test_an_unreadable_lockfile_asserts_nothing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        # Failing to read uv.lock is not evidence of staleness, and guessing
        # would restart a perfectly good daemon on every push.
        daemon = _daemon(tmp_path)
        _running(daemon)
        _record_marker(daemon, "digest-at-start")
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: None)
        _MODULE._drop_stale_graph(daemon)
        assert calls == []

    def test_no_daemon_running_means_nothing_to_stop(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        daemon = _daemon(tmp_path)
        _record_marker(daemon, "digest-at-start")
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: "digest-now")
        monkeypatch.setattr(_MODULE, "_daemon_running", lambda _daemon: False)
        _MODULE._drop_stale_graph(daemon)
        assert calls == []

    def test_the_digest_is_recorded_when_the_daemon_starts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without this the check above can never fire.
        daemon = _daemon(tmp_path)
        _running(daemon)
        monkeypatch.setattr(_MODULE, "_dependency_digest", lambda: "recorded-digest")
        _MODULE._record_bounded_lifetime(daemon)
        assert _MODULE._recorded_dependency_digest(daemon) == "recorded-digest"


class TestRebuildIsReported:
    """The fingerprint catches one staleness trigger; there are others."""

    def test_a_slow_check_is_named_a_rebuild(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reported = _MODULE._report_if_rebuilt(
            _daemon(tmp_path), _MODULE._REBUILD_REPORT_SECONDS
        )
        assert reported is True
        assert "full graph rebuild" in capsys.readouterr().err

    def test_a_warm_check_says_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Every ordinary push takes this path, so a banner here would be
        # noise that trains the reader past the one that matters, and the
        # verdict gates a process scan the push budget cannot afford.
        reported = _MODULE._report_if_rebuilt(
            _daemon(tmp_path), _MODULE._REBUILD_REPORT_SECONDS - 0.01
        )
        assert reported is False
        assert capsys.readouterr().err == ""

    def test_the_dmypy_call_itself_is_what_gets_timed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Only the dmypy call advances the clock, so the report can fire
        # only if the measured span brackets it. A clock read on the wrong
        # side would leave it permanently silent with nothing to say so.
        now = [0.0]
        monkeypatch.setattr(
            _MODULE,
            "time",
            SimpleNamespace(
                monotonic=lambda: now[0],
                time=lambda: now[0],
                sleep=lambda _seconds: None,
            ),
        )
        issued: list[tuple[str, ...]] = []

        def _slow_run(_daemon: object, *args: str, **_kwargs: object) -> int:
            issued.append(args)
            now[0] += _MODULE._REBUILD_REPORT_SECONDS + 1.0
            return 0

        monkeypatch.setattr(_MODULE, "_dmypy", _slow_run)

        assert _MODULE._check_daemon(_daemon(tmp_path)) == 0
        assert issued[-1][:1] == ("run",)
        assert "full graph rebuild" in capsys.readouterr().err


class TestOrphanedServers:
    """A status file holds one pid, so a second server strands the first."""

    @staticmethod
    def _table(
        status_file: Path,
        *pids: int,
        extra: tuple[int, str] | None = None,
    ) -> list[tuple[int, str]]:
        """Build a process table of dmypy servers bound to *status_file*."""
        rows = [
            (pid, f"python.exe -m mypy.dmypy --status-file {status_file} daemon")
            for pid in pids
        ]
        return rows if extra is None else [*rows, extra]

    def test_a_second_server_for_one_status_file_is_an_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daemon = _daemon(tmp_path)
        _running(daemon, pid=100)
        monkeypatch.setattr(_MODULE, "_process_parent", lambda _pid: 99)
        monkeypatch.setattr(
            _MODULE,
            "_process_table",
            lambda: self._table(daemon.status_file, 100, 99, 42),
        )

        assert _MODULE._orphaned_servers(daemon) == [42]

    def test_the_launchers_own_process_is_not_an_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This venv puts a launcher between dmypy and the interpreter, and it
        # carries the same command line; reaping it would fire on every run.
        daemon = _daemon(tmp_path)
        _running(daemon, pid=100)
        monkeypatch.setattr(_MODULE, "_process_parent", lambda _pid: 99)
        monkeypatch.setattr(
            _MODULE, "_process_table", lambda: self._table(daemon.status_file, 100, 99)
        )

        assert _MODULE._orphaned_servers(daemon) == []

    def test_another_worktrees_daemon_is_never_claimed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Killing a sibling worktree's daemon mid-push is worse than leaving
        # an orphan behind, so the match is on this status file alone.
        daemon = _daemon(tmp_path)
        _running(daemon, pid=100)
        sibling = (
            77,
            "python.exe -m mypy.dmypy --status-file C:/other/.dmypy.json daemon",
        )
        monkeypatch.setattr(_MODULE, "_process_parent", lambda _pid: 99)
        monkeypatch.setattr(
            _MODULE,
            "_process_table",
            lambda: self._table(daemon.status_file, 100, extra=sibling),
        )

        assert _MODULE._orphaned_servers(daemon) == []

    def test_a_dead_status_pid_needs_no_launcher_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The status file names a process that is gone, so there is no live
        # daemon whose launcher could be mistaken for an orphan, and asking
        # for a dead process's parent only fails.
        daemon = _daemon(tmp_path)
        _running(daemon, pid=100)

        def _must_not_be_called(_pid: int) -> int | None:
            pytest.fail("looked up the parent of a pid that is not running")

        monkeypatch.setattr(_MODULE, "_process_parent", _must_not_be_called)
        monkeypatch.setattr(
            _MODULE,
            "_process_table",
            lambda: self._table(daemon.status_file, 42),
        )

        assert _MODULE._orphaned_servers(daemon) == [42]

    def test_an_unidentifiable_launcher_claims_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With the daemon alive but its launcher unknown, the launcher is
        # indistinguishable from an orphan, and killing it takes down a
        # working daemon.
        daemon = _daemon(tmp_path)
        _running(daemon, pid=100)
        monkeypatch.setattr(_MODULE, "_process_parent", lambda _pid: None)
        monkeypatch.setattr(
            _MODULE,
            "_process_table",
            lambda: self._table(daemon.status_file, 100, 99),
        )

        assert _MODULE._orphaned_servers(daemon) == []

    def test_no_status_file_claims_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without a pid to compare against, every server looks unreferenced.
        daemon = _daemon(tmp_path)
        monkeypatch.setattr(
            _MODULE, "_process_table", lambda: self._table(daemon.status_file, 42)
        )

        assert _MODULE._orphaned_servers(daemon) == []

    def test_an_orphan_is_reaped_and_announced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        stopped: list[int] = []
        monkeypatch.setattr(_MODULE, "_orphaned_servers", lambda _daemon, **_kw: [42])
        monkeypatch.setattr(_MODULE, "_process_rss_mb", lambda _pid: 2577)
        monkeypatch.setattr(_MODULE, "_stop_holder", stopped.append)

        _REAL_REAP(_daemon(tmp_path))

        assert stopped == [42]
        assert "2577MB" in capsys.readouterr().err

    def test_a_retry_waits_for_the_starting_server(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        calls: list[tuple[str, ...]],
    ) -> None:
        # Retrying before the first server publishes its status file is what
        # starts the second one, so the wait is the fix rather than a comfort.
        daemon = _daemon(tmp_path)
        waited: list[str] = []

        def _record_wait(_daemon: object) -> bool:
            waited.append("waited")
            return True

        monkeypatch.setattr(_MODULE, "_adopt_idle_timeout", lambda _daemon: None)
        monkeypatch.setattr(_MODULE, "_drop_stale_graph", lambda _daemon: None)
        monkeypatch.setattr(_MODULE, "_wait_for_daemon", _record_wait)
        codes = iter([_MODULE._DMYPY_FAILED, 0])
        monkeypatch.setattr(_MODULE, "_dmypy", lambda *_a, **_kw: next(codes))

        assert _MODULE._check_daemon(daemon) == 0
        assert waited == ["waited"], "the retry raced the starting server"


class TestStartLock:
    """Two processes must not both decide to start a server."""

    def test_the_lock_is_taken_and_released(self, tmp_path: Path) -> None:
        daemon = _daemon(tmp_path)
        lock = daemon.status_file.with_suffix(".start.lock")

        with _MODULE._start_lock(daemon) as held:
            assert held is True
            assert lock.is_file()

        assert not lock.exists()

    def test_a_second_holder_proceeds_rather_than_blocking(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fail-open: a lock that could stall a push is worse than the race
        # it prevents, so the loser proceeds and says so via its return.
        daemon = _daemon(tmp_path)
        daemon.status_file.with_suffix(".start.lock").write_text("999")
        monkeypatch.setattr(_MODULE, "_DAEMON_START_GRACE_SECONDS", 0.05)
        monkeypatch.setattr(_MODULE, "_DAEMON_POLL_SECONDS", 0.01)

        with _MODULE._start_lock(daemon) as held:
            assert held is False

    def test_a_lock_older_than_a_build_is_taken_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A process that died holding the lock must not wedge every later
        # run; nothing else would ever remove the file.
        daemon = _daemon(tmp_path)
        lock = daemon.status_file.with_suffix(".start.lock")
        lock.write_text("999")
        monkeypatch.setattr(_MODULE, "_MYPY_TIMEOUT_SECONDS", -1)

        with _MODULE._start_lock(daemon) as held:
            assert held is True

    def test_a_fresh_lock_is_not_stale(self, tmp_path: Path) -> None:
        lock = tmp_path / "fresh.lock"
        lock.write_text("1")

        assert _MODULE._lock_is_stale(lock) is False


class TestSharedDeadline:
    """One ceiling across both attempts, without a zero-second last gasp."""

    def test_a_sub_second_remainder_does_not_become_a_zero_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # int() truncates, so passing the remainder through would hand
        # subprocess a timeout of 0, which expires at once and kills a
        # server that was never asked anything.
        daemon = _daemon(tmp_path)
        _running(daemon)
        now = [0.0]
        monkeypatch.setattr(
            _MODULE,
            "time",
            SimpleNamespace(
                monotonic=lambda: now[0],
                time=lambda: now[0],
                sleep=lambda _seconds: None,
            ),
        )
        timeouts: list[object] = []

        failed: int = _MODULE._DMYPY_FAILED

        def _consume_the_budget(_daemon: object, *_args: str, **kwargs: object) -> int:
            timeouts.append(kwargs.get("timeout"))
            # Leave a fraction of a second, then fail so a retry is due.
            now[0] += _MODULE._MYPY_TIMEOUT_SECONDS - 0.5
            return failed

        monkeypatch.setattr(_MODULE, "_dmypy", _consume_the_budget)
        monkeypatch.setattr(_MODULE, "_drop_stale_graph", lambda _daemon: None)

        _MODULE._check_daemon(daemon)

        assert 0 not in timeouts, "a zero-second timeout reached subprocess"
        assert len(timeouts) == 1, "the sub-second remainder bought a second attempt"


class TestWedgedServerIsKilled:
    """A timeout kills the client; the server has to be killed too."""

    def test_a_timed_out_run_kills_the_server(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daemon = _daemon(tmp_path)
        issued: list[tuple[str, ...]] = []

        def _fake_run(argv: list[str], **kwargs: object) -> object:
            subcommand = tuple(argv[argv.index("--status-file") + 2 :])
            issued.append(subcommand)
            if subcommand[0] == "run":
                raise subprocess.TimeoutExpired(argv, cast("float", kwargs["timeout"]))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run)
        monkeypatch.setattr(_MODULE, "_forget_bounded_lifetime", lambda _daemon: None)
        assert _MODULE._dmypy_result(daemon, "run") is None
        assert ("kill",) in issued, "the wedged server was left running"

    def test_a_timed_out_kill_does_not_recurse(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The kill goes through the same helper, so an unguarded retry would
        # recurse until the stack gave out.
        daemon = _daemon(tmp_path)
        issued: list[tuple[str, ...]] = []

        def _always_times_out(argv: list[str], **kwargs: object) -> object:
            issued.append(tuple(argv[argv.index("--status-file") + 2 :]))
            raise subprocess.TimeoutExpired(argv, cast("float", kwargs["timeout"]))

        monkeypatch.setattr(_MODULE.subprocess, "run", _always_times_out)
        monkeypatch.setattr(_MODULE, "_forget_bounded_lifetime", lambda _daemon: None)
        assert _MODULE._dmypy_result(daemon, "run") is None
        assert issued.count(("kill",)) == 1

    def test_a_clean_run_kills_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daemon = _daemon(tmp_path)
        issued: list[tuple[str, ...]] = []

        def _fake_run(argv: list[str], **_kwargs: object) -> object:
            issued.append(tuple(argv[argv.index("--status-file") + 2 :]))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_MODULE.subprocess, "run", _fake_run)
        result = _MODULE._dmypy_result(daemon, "run")
        assert result is not None
        assert ("kill",) not in issued

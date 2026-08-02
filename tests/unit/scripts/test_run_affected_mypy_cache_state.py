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

The fingerprint only pre-empts the staleness it can see, so one group
covers the rest: a rebuild that happens anyway is at least named, because
a 162s check and a 2s check look identical from outside.

Lives apart from ``test_run_affected_mypy.py`` because that module is
already well past the tests size budget.
"""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> Any:  # type: ignore[explicit-any]
    script = _REPO_ROOT / "scripts" / "run_affected_mypy.py"
    spec = importlib.util.spec_from_file_location("_run_affected_mypy_state", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)  # type: ignore[explicit-any]


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
    """The fingerprint pre-empts one staleness trigger; there are others."""

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
        calls: list[tuple[str, ...]],
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The threshold is only reachable if the elapsed span actually
        # brackets the check; a clock read on the wrong side of it would
        # leave the report permanently silent and nothing else would say so.
        daemon = _daemon(tmp_path)
        _running(daemon)
        monkeypatch.setattr(_MODULE, "_reap_orphaned_servers", lambda _daemon: None)
        ticks = iter([0.0, _MODULE._REBUILD_REPORT_SECONDS + 1.0])
        monkeypatch.setattr(
            _MODULE, "time", SimpleNamespace(monotonic=lambda: next(ticks))
        )

        assert _MODULE._check_daemon(daemon) == 0
        assert calls[-1][:1] == ("run",)
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
        monkeypatch.setattr(_MODULE, "_orphaned_servers", lambda _daemon: [42])
        monkeypatch.setattr(_MODULE, "_process_rss_mb", lambda _pid: 2577)
        monkeypatch.setattr(_MODULE, "_stop_holder", stopped.append)

        _MODULE._reap_orphaned_servers(_daemon(tmp_path))

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
                raise subprocess.TimeoutExpired(argv, cast(float, kwargs["timeout"]))
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
            raise subprocess.TimeoutExpired(argv, cast(float, kwargs["timeout"]))

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

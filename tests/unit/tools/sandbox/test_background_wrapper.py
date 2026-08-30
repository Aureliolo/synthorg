"""Tests for the background-job wrapper shell text builders.

These are pure string builders; correctness is verified by actually
running the built commands through a real shell (subprocess, no Docker
needed) against adversarial command text, rather than asserting on the
exact script string, which would only prove the implementation matches
itself.
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from synthorg.tools.sandbox._background_wrapper import (
    build_kill_command,
    build_liveness_command,
    build_pinned_exec_command,
    build_read_output_command,
    build_read_pid_command,
    build_start_command,
    exit_code_path,
    job_dir,
    output_path,
    pid_path,
)

pytestmark = pytest.mark.unit


def _bash_backgrounding_available() -> bool:
    """Return whether ``bash`` on PATH can background a job and track its pid.

    This wrapper's entire mechanism rests on ``child_pid=$!`` resolving
    to a real pid immediately after ``CMD &``. That is ordinary POSIX
    job control and holds on Linux (CI, the sandbox's own Wolfi base
    image) and under Git Bash's MSYS layer -- but Windows can also
    resolve a bare ``bash`` on PATH to the WSL launcher (``bash.exe``
    or ``wsl.exe``), and WSL's bash, launched non-interactively from a
    Windows process this way, was confirmed directly (varying
    ``setsid``, ``set -m``, stdin mode, and output capture, all with
    the same result) to never populate ``$!`` for a backgrounded job at
    all -- not specific to this module, reproducible with a bare
    ``sleep 5 &``. That is a WSL interop limitation, not something this
    module or its tests can work around, so the whole suite skips
    rather than failing for a reason that has nothing to do with the
    code under test.

    Returns:
        ``True`` when a trivial ``sleep 5 &`` immediately yields a
        non-empty ``$!``.
    """
    probe = subprocess.run(
        ["bash", "-c", 'sleep 5 & child_pid=$!; echo "$child_pid"'],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0 and probe.stdout.strip().isdigit()


_HAS_BASH = pytest.mark.skipif(
    not _bash_backgrounding_available(),
    reason="requires a bash on PATH that can background a job and track its pid",
)


def _to_shell_path(path: Path) -> str:
    """Render *path* the way this test's own ``bash`` needs to see it.

    On POSIX, ``str(path)`` already is that form. On Windows, ``bash``
    may resolve to WSL (needs ``/mnt/c/...``, translated via WSL's own
    ``wslpath``) or to a POSIX layer such as MSYS (``/c/...``); ask the
    same ``bash`` to translate via ``wslpath`` when it has one, else
    fall back to the MSYS convention.

    Returns:
        The path as this test's ``bash`` would resolve it.
    """
    if os.name != "nt":
        return str(path)
    converted = subprocess.run(  # noqa: S603 -- fixed argv, only the path text is interpolated
        ["bash", "-c", f"wslpath -a {_quote_for_probe(str(path))}"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
    )
    if converted.returncode == 0:
        return converted.stdout.strip()
    drive, tail = os.path.splitdrive(str(path))
    return "/" + drive.rstrip(":").lower() + tail.replace("\\", "/")


def _quote_for_probe(text: str) -> str:
    """Single-quote *text* for embedding in a probe command.

    Returns:
        A shell-safe single-quoted token.
    """
    return "'" + text.replace("'", "'\\''") + "'"


_POLL_INTERVAL_SECONDS = 0.05
_POLL_TIMEOUT_SECONDS = 5.0


def _wait_for_nonempty(path: Path, *, timeout: float = _POLL_TIMEOUT_SECONDS) -> str:
    """Poll for *path* to exist with non-empty content.

    A fixed sleep before reading a job's output/exit-code file is a
    guess at how long the wrapper's own background job takes to run
    and flush; that guess is wrong under variable subprocess-spawn
    overhead (observed directly on this machine, where ``bash``
    resolves through the WSL interop shim). Polling for the actual
    condition removes the guess.

    Returns:
        The file's content once non-empty, or ``""`` if *timeout*
        elapses first (the caller's own assertion then reports the
        mismatch against the expected value).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text()
            if content:
                return content
        time.sleep(_POLL_INTERVAL_SECONDS)
    return path.read_text() if path.exists() else ""


def _run_in_shell(
    root: Path, program: str, args: tuple[str, ...]
) -> subprocess.CompletedProcess[str]:
    """Run the built (program, args) with JOBS_ROOT redirected under *root*.

    The wrapper hardcodes ``/tmp/.synthorg-jobs``; tests substitute a
    real temp directory for it via a shell-visible override so the
    command runs unmodified while writing somewhere disposable.

    Returns:
        The completed subprocess.
    """
    return subprocess.run(  # noqa: S603 -- program/args built by the module under test
        [program, *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(root),
        timeout=10,
    )


@pytest.fixture
def patched_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the wrapper's JOBS_ROOT at a disposable, existing directory."""
    root = tmp_path / ".synthorg-jobs"
    root.mkdir()
    monkeypatch.setattr(
        "synthorg.tools.sandbox._background_wrapper.JOBS_ROOT",
        _to_shell_path(root),
    )
    return root


class TestPathHelpers:
    def test_job_dir_scopes_by_job_id(self) -> None:
        assert job_dir("job-1") == "/tmp/.synthorg-jobs/job-1"  # noqa: S108

    def test_output_pid_exit_paths_are_under_job_dir(self) -> None:
        assert output_path("j").startswith(job_dir("j") + "/")
        assert pid_path("j").startswith(job_dir("j") + "/")
        assert exit_code_path("j").startswith(job_dir("j") + "/")
        assert output_path("j") != pid_path("j") != exit_code_path("j")


@_HAS_BASH
class TestBuildStartCommand:
    def test_returns_bash_pipefail_c(self) -> None:
        program, args = build_start_command(
            "j1",
            "echo hi",
            container_cwd="/tmp",  # noqa: S108
        )
        assert program == "bash"
        assert args[:2] == ("-o", "pipefail")
        assert args[2] == "-c"

    def test_prints_only_the_child_pid(self, patched_root: Path) -> None:
        program, args = build_start_command(
            "job-echo", "echo hello-world", container_cwd=_to_shell_path(patched_root)
        )
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 0
        pid_line = result.stdout.strip()
        assert pid_line.isdigit()

        exit_file = patched_root / "job-echo" / "exit_code"
        assert _wait_for_nonempty(exit_file).strip() == "0"
        out = (patched_root / "job-echo" / "output").read_text()
        assert out == "hello-world\n"

    def test_captures_stderr_too(self, patched_root: Path) -> None:
        program, args = build_start_command(
            "job-err", "echo oops 1>&2", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, program, args)
        exit_file = patched_root / "job-err" / "exit_code"
        assert _wait_for_nonempty(exit_file).strip() == "0"
        out = (patched_root / "job-err" / "output").read_text()
        assert out == "oops\n"

    def test_records_exit_code_zero_on_success(self, patched_root: Path) -> None:
        program, args = build_start_command(
            "job-ok", "true", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, program, args)
        code = _wait_for_nonempty(patched_root / "job-ok" / "exit_code").strip()
        assert code == "0"

    def test_records_nonzero_exit_code_on_failure(self, patched_root: Path) -> None:
        program, args = build_start_command(
            "job-fail", "exit 7", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, program, args)
        code = _wait_for_nonempty(patched_root / "job-fail" / "exit_code").strip()
        assert code == "7"

    @pytest.mark.parametrize(
        "command",
        [
            "echo 'has a single quote: '\\''here'\\'''",
            'echo "has double quotes and $vars literally"',
            "printf 'line1\\nline2\\n'",
            "echo a && echo b",
            "echo a; echo b",
            "echo a | cat",
            "echo $(echo nested)",
        ],
    )
    def test_survives_adversarial_command_text(
        self, patched_root: Path, command: str
    ) -> None:
        """The wrapper's own quoting must not corrupt the inner command.

        The highest-risk piece of this feature: a second layer of shell
        quoting around an arbitrary agent-authored command line is where
        injection or truncation bugs would hide.
        """
        program, args = build_start_command(
            "job-adv", command, container_cwd=_to_shell_path(patched_root)
        )
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 0
        code = _wait_for_nonempty(patched_root / "job-adv" / "exit_code").strip()
        assert code == "0"

    def test_still_running_process_has_no_exit_code_yet(
        self, patched_root: Path
    ) -> None:
        program, args = build_start_command(
            "job-slow", "sleep 2", container_cwd=_to_shell_path(patched_root)
        )
        result = _run_in_shell(patched_root, program, args)
        # The wrapper's own start command only returns once the PID
        # file is confirmed (its own internal poll loop), so the job
        # is already running by the time this line executes -- well
        # short of its own 2s sleep, with no further wait needed.
        assert result.stdout.strip().isdigit()
        exit_file = patched_root / "job-slow" / "exit_code"
        assert not exit_file.exists() or exit_file.read_text().strip() == ""


@_HAS_BASH
class TestBuildLivenessCommand:
    def test_reports_running_before_completion(self, patched_root: Path) -> None:
        start_program, start_args = build_start_command(
            "job-live", "sleep 2", container_cwd=_to_shell_path(patched_root)
        )
        started = _run_in_shell(patched_root, start_program, start_args)
        # The PID is only confirmed once the job is running; checking
        # liveness immediately is well short of its own 2s sleep.
        assert started.stdout.strip().isdigit()

        program, args = build_liveness_command("job-live")
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout.strip() == "RUNNING"

    def test_reports_exit_code_after_completion(self, patched_root: Path) -> None:
        start_program, start_args = build_start_command(
            "job-done", "exit 3", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, start_program, start_args)
        _wait_for_nonempty(patched_root / "job-done" / "exit_code")

        program, args = build_liveness_command("job-done")
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout.strip() == "3"

    def test_unknown_job_reports_running(self, patched_root: Path) -> None:
        """No sentinel file at all reads the same as not-yet-finished."""
        program, args = build_liveness_command("ghost-job")
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout.strip() == "RUNNING"


@_HAS_BASH
class TestBuildReadOutputCommand:
    def test_reads_full_output_under_cap(self, patched_root: Path) -> None:
        start_program, start_args = build_start_command(
            "job-read", "echo short", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, start_program, start_args)
        _wait_for_nonempty(patched_root / "job-read" / "exit_code")

        program, args = build_read_output_command("job-read", byte_cap=1000)
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout == "short\n"

    def test_truncates_at_byte_cap(self, patched_root: Path) -> None:
        start_program, start_args = build_start_command(
            "job-big", "printf '0123456789'", container_cwd=_to_shell_path(patched_root)
        )
        _run_in_shell(patched_root, start_program, start_args)
        _wait_for_nonempty(patched_root / "job-big" / "exit_code")

        program, args = build_read_output_command("job-big", byte_cap=4)
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout == "0123"

    def test_rejects_non_positive_byte_cap(self) -> None:
        with pytest.raises(ValueError, match="byte_cap"):
            build_read_output_command("job-x", byte_cap=0)


@_HAS_BASH
class TestBuildKillCommand:
    def test_terminates_the_process_group(self, patched_root: Path) -> None:
        start_program, start_args = build_start_command(
            "job-kill", "sleep 30", container_cwd=_to_shell_path(patched_root)
        )
        started = _run_in_shell(patched_root, start_program, start_args)
        pid = int(started.stdout.strip())

        program, args = build_kill_command(pid, grace_seconds=0.1)
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 0

        # `kill -0` on the now-dead pid fails; that failure is the
        # proof. Poll rather than a fixed sleep: the job's own `wait`
        # needs to observe the kill and reap it before the pid is
        # truly gone rather than a still-`kill -0`-visible zombie.
        exit_file = patched_root / "job-kill" / "exit_code"
        assert _wait_for_nonempty(exit_file)

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        probe_rc = 0
        while time.monotonic() < deadline:
            probe = subprocess.run(  # noqa: S603 -- fixed argv, only the pid is interpolated
                ["bash", "-c", f"kill -0 {pid}"],  # noqa: S607 -- literal partial path
                check=False,
                capture_output=True,
            )
            probe_rc = probe.returncode
            if probe_rc != 0:
                break
            time.sleep(_POLL_INTERVAL_SECONDS)
        assert probe_rc != 0

    def test_killing_an_already_finished_job_does_not_error(self) -> None:
        # A PID this process itself started and already reaped: the
        # kernel cannot have handed it to another process group while
        # this test still holds its exit status. The point is the
        # command itself never raises/exits nonzero over a signal that
        # could not be delivered -- cancelling a job that already
        # finished must not surface as a tool error.
        finished = subprocess.Popen(
            ["bash", "-c", "exit 0"],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        dead_pid = finished.pid
        finished.wait(timeout=5)

        program, args = build_kill_command(dead_pid, grace_seconds=0.01)
        result = subprocess.run(  # noqa: S603 -- program/args built by the module under test
            [program, *args],
            check=False,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0

    def test_rejects_non_positive_pid(self) -> None:
        with pytest.raises(ValueError, match="pid"):
            build_kill_command(0, grace_seconds=1.0)


@_HAS_BASH
class TestBuildPinnedExecCommand:
    def test_returns_setsid_wrapping_bash_pipefail_c(self) -> None:
        program, args = build_pinned_exec_command("job-pin", "bash", ("-c", "echo hi"))
        assert program == "setsid"
        assert args[0] == "bash"
        assert args[1:3] == ("-o", "pipefail")
        assert args[3] == "-c"

    def test_runs_the_command_and_records_a_pid(self, patched_root: Path) -> None:
        program, args = build_pinned_exec_command(
            "job-pin-run", "bash", ("-c", "echo hello-world")
        )
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 0
        assert result.stdout == "hello-world\n"

        pid_file = patched_root / "job-pin-run" / "pid"
        assert pid_file.read_text().strip().isdigit()

    def test_stdout_and_stderr_stay_separate(self, patched_root: Path) -> None:
        """Unlike build_start_command, nothing here merges the streams."""
        program, args = build_pinned_exec_command(
            "job-pin-streams", "bash", ("-c", "echo out; echo err 1>&2")
        )
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout == "out\n"
        assert result.stderr == "err\n"

    def test_nonzero_exit_code_propagates(self, patched_root: Path) -> None:
        program, args = build_pinned_exec_command(
            "job-pin-fail", "bash", ("-c", "exit 7")
        )
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 7

    @pytest.mark.parametrize(
        "command_args",
        [
            ("bash", ("-c", "echo 'has a single quote: '\\''here'\\'''")),
            ("bash", ("-c", 'echo "has double quotes and $vars literally"')),
            ("bash", ("-c", "printf 'line1\\nline2\\n'")),
            ("bash", ("-c", "echo a && echo b")),
            ("bash", ("-c", "echo a; echo b")),
            ("bash", ("-c", "echo a | cat")),
            ("echo", ("plain", "argv", "no", "shell")),
        ],
    )
    def test_survives_adversarial_command_text(
        self, patched_root: Path, command_args: tuple[str, tuple[str, ...]]
    ) -> None:
        command, cmd_args = command_args
        program, args = build_pinned_exec_command("job-pin-adv", command, cmd_args)
        result = _run_in_shell(patched_root, program, args)
        assert result.returncode == 0

    def test_kill_via_recorded_pid_terminates_the_process_group(
        self, patched_root: Path
    ) -> None:
        program, args = build_pinned_exec_command(
            "job-pin-kill", "bash", ("-c", "sleep 30")
        )
        proc = subprocess.Popen(  # noqa: S603 -- program/args built by the module under test
            [program, *args],
            cwd=str(patched_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            pid_file = patched_root / "job-pin-kill" / "pid"
            pid = int(_wait_for_nonempty(pid_file).strip())

            kill_program, kill_args = build_kill_command(pid, grace_seconds=0.1)
            kill_result = _run_in_shell(patched_root, kill_program, kill_args)
            assert kill_result.returncode == 0

            proc.wait(timeout=_POLL_TIMEOUT_SECONDS)
            assert proc.returncode != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


@_HAS_BASH
class TestBuildReadPidCommand:
    def test_reads_a_recorded_pid(self, patched_root: Path) -> None:
        start_program, start_args = build_pinned_exec_command(
            "job-pin-read", "bash", ("-c", "echo hi")
        )
        _run_in_shell(patched_root, start_program, start_args)

        program, args = build_read_pid_command("job-pin-read")
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout.strip().isdigit()

    def test_missing_pidfile_reads_empty(self, patched_root: Path) -> None:
        program, args = build_read_pid_command("job-pin-ghost")
        result = _run_in_shell(patched_root, program, args)
        assert result.stdout.strip() == ""
        assert result.returncode == 0

"""Unit tests for the ``scripts/rewarm_mypy_after_sync.sh`` PostToolUse hook.

The script's two pieces of real logic are the command matcher and the
did-the-sync-succeed guard, and neither has any other backstop: a regression in
the matcher leaves the whole performance fix silently inert, and one in the
guard only wastes a rebuild. Both are exercised here without ever launching the
real detached re-warm, by pointing the hook at a stub ``uv`` on PATH.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from tests._shared import resolve_bash

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "rewarm_mypy_after_sync.sh"

_BASH = resolve_bash()
_BASH_AVAILABLE = pytest.mark.skipif(_BASH is None, reason="bash not available")

# Long enough for a stubbed hook run, short enough to stay well inside the
# pytest-wide 30s ceiling: a subprocess bound above that would turn a hang into
# a silent stall rather than a named failure.
_TIMEOUT_SECONDS = 20
# The hook detaches and returns immediately, so the launch has to be observed
# after the fact. A positive poll returns the instant the stub writes, so the
# long bound only ever costs wall clock on an actual failure. A negative case
# has to wait out a grace period instead, so that one stays short: the child is
# already spawned by the time the hook exits, so anything that will happen has
# happened within a few tens of milliseconds.
# 5s was not enough. Under the pre-push hook the suite runs across 8 xdist
# workers while other gate groups compete for the same cores, and spawning a
# detached shell plus the stub can outlast that on a loaded machine: the hook
# recorded its pid and wrote its log, so the launch decision was correct and
# only the observation timed out. The bound is generous rather than tuned
# because a healthy run returns the instant the stub writes.
#
# Held below the 30s pytest ceiling so a real failure still reports itself. At
# exactly 30s the poll consumed the whole budget and pytest killed the test on
# its timeout first, so the assertion naming the fault never printed and the
# one run that had something to say said nothing.
_LAUNCH_WAIT_SECONDS = 20.0
_NO_LAUNCH_GRACE_SECONDS = 0.5
_POLL_INTERVAL_SECONDS = 0.02


def _stub_uv(tmp_path: Path) -> Path:
    """Return a PATH directory holding a ``uv`` that records its argv.

    The hook's whole job is to decide whether to launch a re-warm, so the test
    needs to observe that decision without paying a multi-minute dmypy rebuild.
    A stub on PATH turns the launch into an observable side effect.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "uv-invoked.txt"
    stub = bin_dir / "uv"
    stub.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> " + f'"{marker}"\n' + "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    return bin_dir


def _clear_lock() -> None:
    """Remove the per-worktree re-warm pidfile before a run.

    The lock deliberately lives in this worktree's git dir, not in a tmp tree,
    because its job is to stop two concurrent re-warms of the same daemon. That
    makes it shared state between tests: without clearing it, the first case to
    launch would leave a pid behind and every later case would correctly decline
    to launch, and the suite would read as a matcher failure.
    """
    git = shutil.which("git")
    if git is None:
        return
    completed = subprocess.run(  # noqa: S603 -- resolved argv, no shell, no untrusted input
        [git, "rev-parse", "--git-path", "synthorg-hooks"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
        timeout=_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        return
    lock = Path(completed.stdout.strip()) / "mypy-rewarm.pid"
    if not lock.is_absolute():
        lock = _REPO_ROOT / lock
    lock.unlink(missing_ok=True)


def _wait_for(path: Path, *, window: float) -> bool:
    """Return whether *path* appears within *window* seconds.

    The hook detaches its child and exits immediately, so checking once on
    return races the launch. Polling a real filesystem effect is the only
    honest way to observe a decision made by a process that has already gone.
    """
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return path.exists()


def _run(
    envelope: object, tmp_path: Path, *, expect_launch: bool = True
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run the hook against *envelope*; report whether it launched a re-warm.

    Args:
        envelope: Hook payload, as an object to serialise or a raw string.
        tmp_path: Per-test directory holding the ``uv`` stub and its marker.
        expect_launch: Whether the caller expects a launch. Only affects how
            long the observation waits, never what it reports.
    """
    assert _BASH is not None
    _clear_lock()
    bin_dir = _stub_uv(tmp_path)
    marker = tmp_path / "uv-invoked.txt"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    payload = envelope if isinstance(envelope, str) else json.dumps(envelope)
    completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell, no untrusted input
        [_BASH, str(_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=_REPO_ROOT,
        check=False,
        env=env,
        timeout=_TIMEOUT_SECONDS,
    )
    window = _LAUNCH_WAIT_SECONDS if expect_launch else _NO_LAUNCH_GRACE_SECONDS
    return completed, _wait_for(marker, window=window)


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        "uv sync",
        "uv add httpx",
        "uv remove httpx",
        # Separator-prefixed form: the regex must match mid-command, not only
        # at the start, or a chained sync would silently stop re-warming.
        "make setup; uv sync",
    ],
)
def test_dependency_changing_commands_trigger_a_rewarm(
    command: str, tmp_path: Path
) -> None:
    completed, launched = _run(
        {"tool_input": {"command": command}, "tool_response": {"exit_code": 0}},
        tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    assert launched, f"{command!r} should have triggered a re-warm"


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "command",
    [
        # By far the most common uv invocation. Matching it would rebuild the
        # graph constantly for no reason, which is worse than not running.
        "uv run pytest",
        "uv lock",
        # Substring traps: the regex is anchored on word boundaries, so a
        # command that merely contains "uv sync" as part of another token must
        # not match.
        "myuv sync",
        "uvx ruff check",
        "git push",
    ],
)
def test_unrelated_commands_do_not_trigger_a_rewarm(
    command: str, tmp_path: Path
) -> None:
    completed, launched = _run(
        {"tool_input": {"command": command}, "tool_response": {"exit_code": 0}},
        tmp_path,
        expect_launch=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not launched, f"{command!r} should not have triggered a re-warm"


@_BASH_AVAILABLE
@pytest.mark.parametrize(
    "response",
    [
        {"exit_code": 1},
        {"isError": True},
        {"interrupted": True},
    ],
)
def test_a_failed_sync_is_skipped(response: dict[str, object], tmp_path: Path) -> None:
    """A sync that failed left the environment as it was, so nothing is stale."""
    completed, launched = _run(
        {"tool_input": {"command": "uv sync"}, "tool_response": response},
        tmp_path,
        expect_launch=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not launched


@_BASH_AVAILABLE
def test_missing_tool_response_still_rewarms(tmp_path: Path) -> None:
    """The documented OpenCode degradation, pinned so it cannot drift silently.

    ``runHookScript`` sends only ``tool_input``, so the success guard has no
    signal there. Re-warming anyway is the harmless direction: at worst one
    wasted rebuild, and only when a daemon is already resident.
    """
    completed, launched = _run({"tool_input": {"command": "uv sync"}}, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert launched


@_BASH_AVAILABLE
def test_malformed_payload_warns_rather_than_failing_silently(tmp_path: Path) -> None:
    """A broken payload must be visible, not indistinguishable from a no-op."""
    completed, launched = _run("not json at all", tmp_path, expect_launch=False)
    assert completed.returncode == 0
    assert not launched
    assert "rewarm_mypy_after_sync" in completed.stderr


@_BASH_AVAILABLE
def test_empty_payload_is_a_silent_no_op(tmp_path: Path) -> None:
    """Nothing on stdin is a legitimate nothing-to-do, not an anomaly."""
    completed, launched = _run("", tmp_path, expect_launch=False)
    assert completed.returncode == 0
    assert not launched
    assert completed.stderr == ""


@_BASH_AVAILABLE
def test_never_interpolates_the_command_into_the_launched_process(
    tmp_path: Path,
) -> None:
    """The payload command is matched as data and never executed.

    It arrives from a Bash tool call, so it is the one attacker-influenceable
    field here; the launched argv must be fixed regardless of what it contains.
    """
    completed, launched = _run(
        {
            "tool_input": {"command": "uv sync && touch " + str(tmp_path / "pwned")},
            "tool_response": {"exit_code": 0},
        },
        tmp_path,
    )
    assert completed.returncode == 0, completed.stderr
    assert launched
    assert not (tmp_path / "pwned").exists()
    recorded = (tmp_path / "uv-invoked.txt").read_text(encoding="utf-8")
    assert "pwned" not in recorded
    assert "--rewarm" in recorded

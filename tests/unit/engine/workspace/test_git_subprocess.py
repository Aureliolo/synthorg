"""Unit tests for the loop-agnostic ``run_git_subprocess`` fallback.

The native ``asyncio.create_subprocess_exec`` path is exercised by the
git-backend integration suite against real git. These tests pin the
Windows-``SelectorEventLoop`` fallback: when the loop cannot spawn a
subprocess (``create_subprocess_exec`` raises ``NotImplementedError``),
the helper runs git via a blocking ``subprocess.run`` on a worker thread
and preserves the same ``(rc, stdout, stderr)`` contract.

They also pin the failure-code contract: an absent binary, a spawn error
and a timeout each carry their own return code, so an operator reading a
provisioning error can tell "git is not installed" from "git failed".
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.engine.workspace._git_subprocess import (
    GIT_RC_BINARY_NOT_FOUND,
    GIT_RC_SPAWN_FAILED,
    GIT_RC_TIMED_OUT,
    describe_git_failure,
    run_git_subprocess,
)
from synthorg.observability.events.workspace import GIT_BACKEND_PROVISION_START

pytestmark = pytest.mark.unit


async def test_falls_back_to_thread_when_loop_cannot_spawn() -> None:
    """A loop that cannot spawn subprocesses routes git through a thread."""
    completed = subprocess.CompletedProcess(
        args=["git", "status"],
        returncode=0,
        stdout=b"nothing to commit\n",
        stderr=b"",
    )
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(subprocess, "run", return_value=completed) as run_mock,
    ):
        rc, stdout, stderr = await run_git_subprocess(
            Path(),
            "status",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert (rc, stdout, stderr) == (0, "nothing to commit", "")
    # The fallback shells out to git with a list argv (no shell) and never
    # touches the event loop's subprocess machinery.
    run_mock.assert_called_once()
    assert run_mock.call_args.args[0] == ["git", "status"]


async def test_thread_fallback_reports_timeout() -> None:
    """A timeout in the thread fallback carries the timeout return code."""
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(
            subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5.0),
        ),
    ):
        rc, stdout, stderr = await run_git_subprocess(
            Path(),
            "status",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert rc == GIT_RC_TIMED_OUT
    assert stdout == ""
    assert "timed out" in stderr


async def test_thread_fallback_reports_missing_git() -> None:
    """A missing git binary is its own return code, not a generic failure."""
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(
            subprocess,
            "run",
            side_effect=FileNotFoundError("git not found"),
        ),
    ):
        rc, stdout, stderr = await run_git_subprocess(
            Path(),
            "status",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert rc == GIT_RC_BINARY_NOT_FOUND
    assert stdout == ""
    assert "not on PATH" in stderr


async def test_thread_fallback_separates_spawn_error_from_missing_binary() -> None:
    """A non-``FileNotFoundError`` spawn failure keeps its own return code."""
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(
            subprocess,
            "run",
            side_effect=PermissionError("git is not executable"),
        ),
    ):
        rc, _stdout, stderr = await run_git_subprocess(
            Path(),
            "status",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert rc == GIT_RC_SPAWN_FAILED
    assert "not on PATH" not in stderr


async def test_native_path_reports_missing_git() -> None:
    """The native spawn path classifies a missing binary the same way.

    The dogfood failure took this branch, not the thread fallback, and
    reported it as an indistinguishable ``rc=-1``.
    """
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("git not found"),
    ):
        rc, stdout, stderr = await run_git_subprocess(
            Path(),
            "init",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert rc == GIT_RC_BINARY_NOT_FOUND
    assert stdout == ""
    assert "not on PATH" in stderr


@pytest.mark.parametrize(
    "code",
    [GIT_RC_BINARY_NOT_FOUND, GIT_RC_SPAWN_FAILED, GIT_RC_TIMED_OUT],
)
def test_every_failure_code_describes_itself(code: int) -> None:
    """Each sentinel renders a distinct operator-facing cause."""
    described = describe_git_failure(code)
    assert described is not None
    assert described.strip()


def test_describe_git_failure_ignores_ordinary_exit_codes() -> None:
    """A real git exit code is not a spawn failure and describes nothing."""
    assert describe_git_failure(0) is None
    assert describe_git_failure(128) is None


def test_failure_codes_are_distinct() -> None:
    """The three causes never collapse onto one another.

    The defect this replaces overloaded ``-1`` across all three, so a
    missing binary was indistinguishable from a failing git command.
    """
    codes = {GIT_RC_BINARY_NOT_FOUND, GIT_RC_SPAWN_FAILED, GIT_RC_TIMED_OUT}
    assert len(codes) == 3
    assert all(code < 0 for code in codes)

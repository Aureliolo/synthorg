"""Unit tests for the loop-agnostic ``run_git_subprocess`` fallback.

The native ``asyncio.create_subprocess_exec`` path is exercised by the
git-backend integration suite against real git. These tests pin the
Windows-``SelectorEventLoop`` fallback: when the loop cannot spawn a
subprocess (``create_subprocess_exec`` raises ``NotImplementedError``),
the helper runs git via a blocking ``subprocess.run`` on a worker thread
and preserves the same ``(rc, stdout, stderr)`` contract.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.engine.workspace import _git_subprocess
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
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
        patch.object(
            _git_subprocess.subprocess, "run", return_value=completed
        ) as run_mock,
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
    """A timeout in the thread fallback returns the ``(-1, "", msg)`` contract."""
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(
            _git_subprocess.subprocess,
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

    assert rc == -1
    assert stdout == ""
    assert "timed out" in stderr


async def test_thread_fallback_reports_missing_git() -> None:
    """A missing git binary in the fallback returns the spawn-failure contract."""
    with (
        patch(
            "asyncio.create_subprocess_exec",
            side_effect=NotImplementedError,
        ),
        patch.object(
            _git_subprocess.subprocess,
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

    assert rc == -1
    assert stdout == ""
    assert "failed to spawn" in stderr

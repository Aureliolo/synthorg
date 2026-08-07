"""Unit tests for the loop-agnostic ``run_git_subprocess`` fallback.

The native ``asyncio.create_subprocess_exec`` path is exercised by the
git-backend integration suite against real git. These tests pin the
Windows-``SelectorEventLoop`` fallback: when the loop cannot spawn a
subprocess (``create_subprocess_exec`` raises ``NotImplementedError``),
the helper runs git via a blocking ``subprocess.run`` on a worker thread
and preserves the same ``(rc, stdout, stderr)`` contract.

They also pin the failure-code contract: an absent binary, a spawn error
and a timeout each carry their own return code, so an operator reading a
provisioning error can tell "git is not installed" from "git failed";
and the environment every git command runs under, which is what decides
how much of the host a workspace command trusts and where a forge
credential is allowed to appear.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from synthorg.core.git_env import GIT_HARDENING_OVERRIDES
from synthorg.engine.workspace._git_subprocess import (
    GIT_RC_BINARY_NOT_FOUND,
    GIT_RC_SPAWN_FAILED,
    GIT_RC_TIMED_OUT,
    describe_git_failure,
    run_git_subprocess,
)
from synthorg.observability.events.workspace import GIT_BACKEND_PROVISION_START

pytestmark = pytest.mark.unit


async def _spawn_env(**kwargs: object) -> dict[str, str]:
    """Run one git command through the thread fallback and return its env.

    Args:
        **kwargs: Extra keyword arguments for ``run_git_subprocess``.

    Returns:
        The environment the fallback handed to ``subprocess.run``.
    """
    completed = subprocess.CompletedProcess(
        args=["git", "status"], returncode=0, stdout=b"", stderr=b""
    )
    with (
        patch("asyncio.create_subprocess_exec", side_effect=NotImplementedError),
        patch.object(subprocess, "run", return_value=completed) as run_mock,
    ):
        await run_git_subprocess(
            Path(),
            "status",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
            **kwargs,  # type: ignore[arg-type]  # forwarded verbatim to the helper
        )
    return dict(run_mock.call_args.kwargs["env"])


class TestTheEnvironmentGitRunsUnder:
    async def test_the_hardening_overrides_are_applied(self) -> None:
        """This path spawns git over agent-authored content, like the tools do.

        Both were written against the same four overrides, and stating
        them once is what stops one path being hardened and the other not.
        """
        env = await _spawn_env()

        for key, value in GIT_HARDENING_OVERRIDES.items():
            assert env[key] == value

    async def test_inherited_git_variables_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``GIT_DIR`` from a parent hook would override cwd-based discovery."""
        monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")

        env = await _spawn_env()

        assert "GIT_DIR" not in env

    async def test_the_hardening_beats_an_inherited_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An inherited prompt setting must not re-open a credential prompt."""
        monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")

        env = await _spawn_env()

        assert env["GIT_TERMINAL_PROMPT"] == "0"

    async def test_per_invocation_config_travels_in_the_environment(self) -> None:
        """Not in argv, which every process on the host can read.

        And not in ``.git/config``, which outlives the command inside an
        agent-writable workspace.
        """
        env = await _spawn_env(config={"url.https://x:tok@h/.insteadOf": "https://h/"})

        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_CONFIG_KEY_0"] == "url.https://x:tok@h/.insteadOf"
        assert env["GIT_CONFIG_VALUE_0"] == "https://h/"

    async def test_no_config_sets_no_count(self) -> None:
        """A stray count with no keys makes git refuse the command outright."""
        env = await _spawn_env()

        assert "GIT_CONFIG_COUNT" not in env

    async def test_the_config_survives_the_disabled_config_files(self) -> None:
        """``GIT_CONFIG_GLOBAL``/``NOSYSTEM`` cut the files, not this channel."""
        env = await _spawn_env(config={"http.version": "HTTP/1.1"})

        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_CONFIG_KEY_0"] == "http.version"


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

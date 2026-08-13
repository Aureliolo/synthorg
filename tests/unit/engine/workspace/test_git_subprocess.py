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

from synthorg.core.git_env import (
    GIT_HARDENING_OVERRIDES,
    LOCAL_TRANSPORT_GIT_CONFIG,
    SHARED_GROUP_GIT_CONFIG,
)
from synthorg.engine.workspace._git_subprocess import (
    GIT_RC_BINARY_NOT_FOUND,
    GIT_RC_MISSING_REPO_ROOT,
    GIT_RC_SPAWN_FAILED,
    GIT_RC_TIMED_OUT,
    GIT_STDERR_TAIL_CHARS,
    _redact_arg,
    describe_git_failure,
    git_stderr_summary,
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


def _decoded_config(env: dict[str, str]) -> dict[str, str]:
    """Read the ``GIT_CONFIG_*`` triplets back into the mapping git sees.

    Decoding beats asserting on a fixed index: the pairs are rendered in
    mapping order, so a positional assertion pins where a key landed
    rather than that git receives it.

    Returns:
        Config keys mapped to their values, empty when no count is set.
    """
    count = int(env.get("GIT_CONFIG_COUNT", "0"))
    return {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(count)
    }


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

        assert _decoded_config(env)["url.https://x:tok@h/.insteadOf"] == "https://h/"

    async def test_the_file_transport_stays_reachable(self) -> None:
        """An embedded workspace IS a bare repo at a local path.

        ``GIT_PROTOCOL_FROM_USER=0`` drops the file transport to git's
        ``user`` policy, which refuses it outright, so every clone, fetch
        and push this path makes would fail ``rc=128``.
        """
        assert _decoded_config(await _spawn_env())["protocol.file.allow"] == "always"

    async def test_a_caller_key_does_not_displace_the_transport_allowance(self) -> None:
        """Both share one ``GIT_CONFIG_COUNT``; rendering twice loses one."""
        decoded = _decoded_config(await _spawn_env(config={"http.version": "HTTP/1.1"}))

        assert decoded["http.version"] == "HTTP/1.1"
        assert decoded.keys() >= LOCAL_TRANSPORT_GIT_CONFIG.keys()

    async def test_the_repository_is_shared_with_the_group(self) -> None:
        """The sandbox reads the workspace through the backend's group.

        Left at the process umask, everything git creates (``.git`` itself, a
        worktree root, a checked-out tree) is a directory the sandbox can read
        and traverse but never write, so a build reports a read-only
        filesystem on a workspace the design calls writable.
        """
        assert _decoded_config(await _spawn_env())["core.sharedRepository"] == "group"

    async def test_a_caller_key_does_not_displace_the_group_sharing(self) -> None:
        """Same single-``GIT_CONFIG_COUNT`` hazard as the transport allowance."""
        decoded = _decoded_config(await _spawn_env(config={"http.version": "HTTP/1.1"}))

        assert decoded.keys() >= SHARED_GROUP_GIT_CONFIG.keys()

    async def test_the_config_survives_the_disabled_config_files(self) -> None:
        """``GIT_CONFIG_GLOBAL``/``NOSYSTEM`` cut the files, not this channel."""
        env = await _spawn_env(config={"http.version": "HTTP/1.1"})

        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert _decoded_config(env)["http.version"] == "HTTP/1.1"


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

    This is the branch a real spawn takes, not the thread fallback, and it
    is where a missing binary would otherwise surface as an
    indistinguishable ``rc=-1``.
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


async def test_a_missing_repo_root_is_not_reported_as_a_missing_binary(
    tmp_path: Path,
) -> None:
    """A cwd that is not there raises the same type as an absent binary.

    On POSIX both are ``FileNotFoundError``, so trusting the exception type
    would tell an operator to install git that is already installed. The
    workspace failure this separates is the common one: the project repo
    was never provisioned.
    """
    absent = tmp_path / "projects" / "never-provisioned"
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("No such file or directory"),
    ):
        rc, _stdout, stderr = await run_git_subprocess(
            absent,
            "branch",
            cmd_timeout=5.0,
            log_event=GIT_BACKEND_PROVISION_START,
        )

    assert rc == GIT_RC_MISSING_REPO_ROOT
    assert "not on PATH" not in stderr
    assert "does not exist" in stderr


@pytest.mark.parametrize(
    "code",
    [
        GIT_RC_BINARY_NOT_FOUND,
        GIT_RC_SPAWN_FAILED,
        GIT_RC_TIMED_OUT,
        GIT_RC_MISSING_REPO_ROOT,
    ],
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
    codes = {
        GIT_RC_BINARY_NOT_FOUND,
        GIT_RC_SPAWN_FAILED,
        GIT_RC_TIMED_OUT,
        GIT_RC_MISSING_REPO_ROOT,
    }
    assert len(codes) == 4
    assert all(code < 0 for code in codes)


class TestGitExplainsItsOwnFailures:
    """A return code alone does not identify a git failure.

    ``describe_git_failure`` covers only the codes git never produced, so
    for every code git DOES produce, its stderr is the sole account of
    what happened. A live provisioning push logged ``rc=128`` with that
    stream discarded, and the failure could not be diagnosed at all.
    """

    def test_git_gets_the_last_word(self) -> None:
        summary = git_stderr_summary(
            "fatal: The current branch main has no upstream branch\n"
        )
        assert summary is not None
        assert "no upstream branch" in summary

    def test_silence_is_reported_as_silence(self) -> None:
        """``None``, not an empty string: there is nothing to append."""
        assert git_stderr_summary("   \n  ") is None

    def test_the_tail_is_kept_because_that_is_where_fatal_lands(self) -> None:
        """Git precedes its verdict with progress output."""
        noise = "Enumerating objects: 1\n" * 200
        summary = git_stderr_summary(f"{noise}fatal: refusing to merge histories")
        assert summary is not None
        assert "refusing to merge histories" in summary
        assert len(summary) <= GIT_STDERR_TAIL_CHARS

    def test_a_credential_in_the_stream_does_not_reach_the_log(self) -> None:
        summary = git_stderr_summary(
            "fatal: Authentication failed for "
            "'https://x-access-token:ghp_supersecret@example.test/o/r.git/'"
        )
        assert summary is not None
        assert "ghp_supersecret" not in summary

    def test_a_credential_outside_a_url_is_masked_too(self) -> None:
        """An argument does not have to be a URL to carry a token."""
        redacted = _redact_arg("http.extraHeader=Authorization: Bearer ghp_supersecret")
        assert "ghp_supersecret" not in redacted

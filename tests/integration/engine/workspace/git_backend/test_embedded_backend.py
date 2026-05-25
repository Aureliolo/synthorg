"""Integration tests for ``EmbeddedGitBackend`` (real git in tmp_path).

These tests spawn real ``git`` subprocesses (provision, push, fetch,
worktree). On Windows, each subprocess creation costs ~200ms and the
provision step alone fires ~9 subprocesses, so the test cannot fit
under the 6.0s per-test unit wall-clock guard under ``--count 2``
isolation-gate contention. Per the comment in ``tests/conftest.py``,
work like this ("real subprocess, real network, real heavy I/O")
belongs in ``tests/integration/`` rather than ``tests/unit/``.
"""

import asyncio
import os
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend import EmbeddedGitBackend
from tests._shared import FakeClock

pytestmark = pytest.mark.integration


def _clean_env() -> dict[str, str]:
    """Strip ``GIT_*`` env vars so child git commands resolve via *cwd* alone.

    Running under a pre-push hook inherits ``GIT_DIR`` / ``GIT_WORK_TREE``
    which would otherwise make ``git`` operate on the synthorg repo
    instead of the per-test workspace.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


async def _git(cwd: Path, *args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        env=_clean_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    rc = await proc.wait()
    assert rc == 0, f"git {args} failed (rc={rc})"


def _backend(base: Path) -> EmbeddedGitBackend:
    return EmbeddedGitBackend(
        base_root=base,
        embedded_subdir="git-repos",
        cmd_timeout=30.0,
        clock=FakeClock(),
    )


class TestEmbeddedGitBackend:
    async def test_provision_creates_bare_and_working_tree(
        self, tmp_path: Path
    ) -> None:
        base = tmp_path / "base"
        ws = base / "projects" / "p1"
        backend = _backend(base)

        result = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )

        assert result.newly_created is True
        assert result.default_branch == "main"
        assert Path(result.repo_root) == ws
        assert (base / "git-repos" / "p1.git").exists()
        assert (ws / ".git").exists()

    async def test_provision_is_idempotent(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        ws = base / "projects" / "p1"
        backend = _backend(base)

        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )
        again = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )
        assert again.newly_created is False

    async def test_push_and_fetch_round_trip(self, tmp_path: Path) -> None:
        base = tmp_path / "base"
        ws = base / "projects" / "p1"
        backend = _backend(base)
        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )

        await _git(ws, "checkout", "-b", "feature")
        (ws / "file.txt").write_text("hello\n")
        await _git(ws, "add", "file.txt")
        # Set a repo-local identity so ``git commit`` succeeds on CI /
        # dev hosts where no global ``user.name`` / ``user.email`` is
        # configured; the values are arbitrary but must be set.
        await _git(ws, "config", "user.email", "synthorg-tests@example.invalid")
        await _git(ws, "config", "user.name", "SynthOrg Test")
        await _git(ws, "commit", "-m", "work")

        push = await backend.push(
            project_id=NotBlankStr("p1"),
            repo_root=ws,
            branch=NotBlankStr("feature"),
            base_branch=NotBlankStr("main"),
        )
        assert push.branch == "feature"
        assert len(push.head_sha) >= 7

        fetched = await backend.fetch(
            project_id=NotBlankStr("p1"),
            repo_root=ws,
            branch=NotBlankStr("feature"),
        )
        assert fetched.updated_refs == (NotBlankStr("feature"),)

    async def test_configure_identity_refuses_linked_worktree(
        self, tmp_path: Path
    ) -> None:
        # Direct exercise of the assert_standalone_repo guard inside
        # _configure_identity. Without the guard, ``git config
        # user.email synthorg-bot@synthorg.local`` against a linked
        # worktree mutates the SHARED config of the parent repo (the
        # exact leak that rewrote the operator's identity across every
        # sibling worktree). The public provision() path short-circuits
        # via is_git_repo BEFORE reaching this method, so this is a
        # defence-in-depth test against future callers that bypass that
        # check or against an unforeseen path where is_git_repo would
        # return false on a linked worktree.
        from synthorg.engine.errors import GitBackendProvisionError
        from synthorg.engine.workspace.git_backend._git_ops import configure_identity

        host_repo = tmp_path / "host"
        host_repo.mkdir()
        await _git(host_repo, "init", "--initial-branch=main")
        await _git(host_repo, "config", "user.email", "host@example.invalid")
        await _git(host_repo, "config", "user.name", "Host")
        (host_repo / "seed.txt").write_text("seed\n")
        await _git(host_repo, "add", "seed.txt")
        await _git(host_repo, "commit", "-m", "seed")
        # Carve a linked worktree off the host repo. Its `.git` is a
        # FILE pointing at host_repo/.git/worktrees/<name>; `git
        # config` writes there bubble up to host_repo/.git/config.
        linked_tree = tmp_path / "linked"
        await _git(host_repo, "worktree", "add", "-B", "wt-branch", str(linked_tree))
        with pytest.raises(GitBackendProvisionError) as exc_info:
            await configure_identity(
                linked_tree,
                cmd_timeout=30.0,
                fail_exc=GitBackendProvisionError,
                project_id="p1",
            )
        assert "shared" in str(exc_info.value).lower()
        # The parent's user.email must still equal the value we set
        # above; the bot identity must NOT have leaked into the host
        # repo's shared config.
        proc = await asyncio.create_subprocess_exec(
            "git",
            "config",
            "user.email",
            cwd=str(host_repo),
            env=_clean_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        assert stdout.decode().strip() == "host@example.invalid"

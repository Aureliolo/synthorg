"""Unit tests for ``EmbeddedGitBackend`` (real git in tmp_path)."""

import asyncio
import os
from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.git_backend import EmbeddedGitBackend

pytestmark = pytest.mark.unit


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

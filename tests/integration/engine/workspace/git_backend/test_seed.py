"""Integration tests for ``GitBackend.seed`` (brownfield source import).

The embedded and local-path backends run real git in ``tmp_path`` so the
shared fetch/reset import mechanics are exercised end-to-end offline. The
external-remote backend mocks ``run_git_subprocess`` (no live forge) and
asserts seed imports the source then pushes.

Marked ``integration`` (not ``unit``) because every test shells out to
the system ``git`` binary multiple times via
``asyncio.create_subprocess_exec``; on Windows under xdist contention
that legitimately exceeds the unit-tier wall-clock budget.
"""

import asyncio
import os
from pathlib import Path

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendSeedError
from synthorg.engine.workspace.git_backend import (
    EmbeddedGitBackend,
    LocalPathGitBackend,
)
from synthorg.engine.workspace.git_backend.protocol import (
    ResolvedSource,
    SourceKind,
)
from tests._shared import FakeClock

pytestmark = pytest.mark.integration


def _clean_env() -> dict[str, str]:
    """Strip ``GIT_*`` env so child git resolves via *cwd* alone."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


async def _git(cwd: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        env=_clean_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    assert proc.returncode == 0, f"git {args} failed (rc={proc.returncode})"
    return stdout.decode().strip()


async def _make_source_repo(path: Path, *, branch: str = "master") -> str:
    """Build a source repo with one commit; return its head SHA."""
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
    await _git(path, "init", "--initial-branch", branch)
    await _git(path, "config", "user.email", "src@example.com")
    await _git(path, "config", "user.name", "Source Author")
    await asyncio.to_thread((path / "README.md").write_text, "# imported\n")
    await asyncio.to_thread((path / "main.py").write_text, "print('hi')\n")
    await _git(path, "add", "-A")
    await _git(path, "commit", "-m", "initial source commit")
    return await _git(path, "rev-parse", "HEAD")


def _local_source(path: Path) -> ResolvedSource:
    return ResolvedSource(
        fetch_url=NotBlankStr(str(path)),
        source_kind=SourceKind.LOCAL_PATH,
    )


class TestEmbeddedSeed:
    async def test_seed_imports_source_history(self, tmp_path: Path) -> None:
        src_head = await _make_source_repo(tmp_path / "source")
        base = tmp_path / "base"
        ws = base / "projects" / "p1"
        backend = EmbeddedGitBackend(
            base_root=base,
            embedded_subdir="git-repos",
            cmd_timeout=30.0,
            clock=FakeClock(),
        )
        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )

        result = await backend.seed(
            project_id=NotBlankStr("p1"),
            repo_root=ws,
            source=_local_source(tmp_path / "source"),
            default_branch=NotBlankStr("main"),
        )

        assert result.head_sha == src_head
        assert result.source_kind is SourceKind.LOCAL_PATH
        assert (ws / "README.md").exists()
        assert (ws / "main.py").exists()
        # The imported head was pushed to the project's bare repo.
        bare = base / "git-repos" / "p1.git"
        assert await _git(bare, "rev-parse", "main") == src_head

    async def test_seed_rejects_non_empty_workspace(self, tmp_path: Path) -> None:
        await _make_source_repo(tmp_path / "source")
        base = tmp_path / "base"
        ws = base / "projects" / "p1"
        backend = EmbeddedGitBackend(
            base_root=base,
            embedded_subdir="git-repos",
            cmd_timeout=30.0,
            clock=FakeClock(),
        )
        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=ws,
            default_branch=NotBlankStr("main"),
        )
        # First seed populates the workspace.
        await backend.seed(
            project_id=NotBlankStr("p1"),
            repo_root=ws,
            source=_local_source(tmp_path / "source"),
            default_branch=NotBlankStr("main"),
        )

        with pytest.raises(GitBackendSeedError):
            await backend.seed(
                project_id=NotBlankStr("p1"),
                repo_root=ws,
                source=_local_source(tmp_path / "source"),
                default_branch=NotBlankStr("main"),
            )


class TestLocalPathSeed:
    async def test_seed_imports_into_per_project_repo(self, tmp_path: Path) -> None:
        src_head = await _make_source_repo(tmp_path / "source")
        repo_base = tmp_path / "repos"
        backend = LocalPathGitBackend(
            local_repo_path=str(repo_base),
            cmd_timeout=30.0,
            clock=FakeClock(),
        )
        provisioned = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "unused",
            default_branch=NotBlankStr("main"),
        )
        repo_root = Path(provisioned.repo_root)

        result = await backend.seed(
            project_id=NotBlankStr("p1"),
            repo_root=repo_root,
            source=_local_source(tmp_path / "source"),
            default_branch=NotBlankStr("main"),
        )

        assert result.head_sha == src_head
        assert (repo_root / "README.md").exists()
        assert (repo_root / "main.py").exists()

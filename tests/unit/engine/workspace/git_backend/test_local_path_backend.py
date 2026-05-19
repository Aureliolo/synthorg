"""Unit tests for ``LocalPathGitBackend``."""

from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend import LocalPathGitBackend

pytestmark = pytest.mark.unit


def _backend(repo: Path) -> LocalPathGitBackend:
    return LocalPathGitBackend(
        local_repo_path=str(repo),
        cmd_timeout=30.0,
        clock=FakeClock(),
    )


class TestLocalPathGitBackend:
    async def test_provision_initialises_empty_dir(self, tmp_path: Path) -> None:
        repo = tmp_path / "byo"
        backend = _backend(repo)

        result = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        assert result.newly_created is True
        assert Path(result.repo_root) == repo
        assert (repo / ".git").exists()

    async def test_provision_idempotent_on_existing_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "byo"
        backend = _backend(repo)
        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        again = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        assert again.newly_created is False

    async def test_non_git_non_empty_dir_rejected(self, tmp_path: Path) -> None:
        repo = tmp_path / "byo"
        repo.mkdir()
        (repo / "stray.txt").write_text("not a repo\n")
        backend = _backend(repo)

        with pytest.raises(GitBackendConfigError, match="not a git"):
            await backend.provision(
                project_id=NotBlankStr("p1"),
                workspace_path=tmp_path / "ignored",
                default_branch=NotBlankStr("main"),
            )

    async def test_push_returns_head_sha(self, tmp_path: Path) -> None:
        repo = tmp_path / "byo"
        backend = _backend(repo)
        await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        push = await backend.push(
            project_id=NotBlankStr("p1"),
            repo_root=repo,
            branch=NotBlankStr("main"),
            base_branch=NotBlankStr("main"),
        )
        assert push.branch == "main"
        assert len(push.head_sha) >= 7

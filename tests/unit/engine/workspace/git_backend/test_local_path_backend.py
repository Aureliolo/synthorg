"""Unit tests for ``LocalPathGitBackend``."""

from pathlib import Path

import pytest
from tests._shared import FakeClock

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import GitBackendConfigError
from synthorg.engine.workspace.git_backend import LocalPathGitBackend

pytestmark = pytest.mark.unit


def _backend(base: Path) -> LocalPathGitBackend:
    """Build a backend over a BASE directory under which per-project repos live."""
    return LocalPathGitBackend(
        local_repo_path=str(base),
        cmd_timeout=30.0,
        clock=FakeClock(),
    )


class TestLocalPathGitBackend:
    async def test_provision_initialises_empty_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "byo"
        backend = _backend(base)

        result = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        repo = base / "p1"
        assert result.newly_created is True
        assert Path(result.repo_root) == repo
        assert (repo / ".git").exists()

    async def test_provision_idempotent_on_existing_repo(self, tmp_path: Path) -> None:
        base = tmp_path / "byo"
        backend = _backend(base)
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
        base = tmp_path / "byo"
        repo = base / "p1"
        repo.mkdir(parents=True)
        (repo / "stray.txt").write_text("not a repo\n")
        backend = _backend(base)

        with pytest.raises(GitBackendConfigError, match="not a git"):
            await backend.provision(
                project_id=NotBlankStr("p1"),
                workspace_path=tmp_path / "ignored",
                default_branch=NotBlankStr("main"),
            )

    async def test_push_returns_head_sha(self, tmp_path: Path) -> None:
        base = tmp_path / "byo"
        backend = _backend(base)
        result = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        push = await backend.push(
            project_id=NotBlankStr("p1"),
            repo_root=Path(result.repo_root),
            branch=NotBlankStr("main"),
            base_branch=NotBlankStr("main"),
        )
        assert push.branch == "main"
        assert len(push.head_sha) >= 7

    async def test_distinct_projects_get_distinct_repos(self, tmp_path: Path) -> None:
        """Per-project isolation: two project IDs do NOT share one repo root."""
        base = tmp_path / "byo"
        backend = _backend(base)
        r1 = await backend.provision(
            project_id=NotBlankStr("p1"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        r2 = await backend.provision(
            project_id=NotBlankStr("p2"),
            workspace_path=tmp_path / "ignored",
            default_branch=NotBlankStr("main"),
        )
        assert r1.repo_root != r2.repo_root
        assert Path(r1.repo_root) == base / "p1"
        assert Path(r2.repo_root) == base / "p2"
        assert r1.newly_created is True
        assert r2.newly_created is True

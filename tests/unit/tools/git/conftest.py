"""Fixtures for git tool tests."""

import os
import subprocess
from pathlib import Path  # noqa: TC003 — used at runtime

import pytest

from ai_company.tools.git_tools import (
    GitBranchTool,
    GitCloneTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
    "GIT_TERMINAL_PROMPT": "0",
}


def _run_git(args: list[str], cwd: Path) -> None:
    """Run a git command synchronously."""
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Bare workspace directory (no git repo)."""
    return tmp_path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialized git repo with one commit."""
    _run_git(["init"], tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("# Test\n")
    _run_git(["add", "."], tmp_path)
    _run_git(["commit", "-m", "initial commit"], tmp_path)
    return tmp_path


@pytest.fixture
def empty_git_repo(tmp_path: Path) -> Path:
    """Initialized git repo with no commits."""
    _run_git(["init"], tmp_path)
    return tmp_path


@pytest.fixture
def git_repo_with_changes(git_repo: Path) -> Path:
    """Git repo with uncommitted changes."""
    (git_repo / "new_file.txt").write_text("hello\n")
    (git_repo / "README.md").write_text("# Modified\n")
    return git_repo


# ── Tool factory fixtures ────────────────────────────────────────


@pytest.fixture
def status_tool(git_repo: Path) -> GitStatusTool:
    """GitStatusTool bound to the test repo."""
    return GitStatusTool(workspace=git_repo)


@pytest.fixture
def log_tool(git_repo: Path) -> GitLogTool:
    """GitLogTool bound to the test repo."""
    return GitLogTool(workspace=git_repo)


@pytest.fixture
def diff_tool(git_repo: Path) -> GitDiffTool:
    """GitDiffTool bound to the test repo."""
    return GitDiffTool(workspace=git_repo)


@pytest.fixture
def branch_tool(git_repo: Path) -> GitBranchTool:
    """GitBranchTool bound to the test repo."""
    return GitBranchTool(workspace=git_repo)


@pytest.fixture
def commit_tool(git_repo: Path) -> GitCommitTool:
    """GitCommitTool bound to the test repo."""
    return GitCommitTool(workspace=git_repo)


@pytest.fixture
def clone_tool(workspace: Path) -> GitCloneTool:
    """GitCloneTool bound to a bare workspace."""
    return GitCloneTool(workspace=workspace)

"""Fixtures for git tool tests."""

import os
import subprocess
from pathlib import Path

import pytest

import synthorg.tools.git_tools as git_tools_module
from synthorg.tools._git_base import _GIT_DISCOVERY_VARS
from synthorg.tools.git_tools import (
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
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_PROTOCOL_FROM_USER": "0",
}
# Strip git discovery vars so fixtures use cwd-based repo detection,
# not stale env vars inherited from e.g. git push → pre-push hook.
for _key in _GIT_DISCOVERY_VARS:
    _GIT_ENV.pop(_key, None)


def _run_git(args: list[str], cwd: Path) -> None:
    """Run a git command synchronously."""
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        env=_GIT_ENV,
    )


def _run_git_output(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_GIT_ENV,
    )
    return result.stdout.strip()


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip git discovery env vars so tools use cwd-based repo detection.

    Without this, env vars like ``GIT_DIR`` inherited from
    ``git push`` pre-push hooks cause tools to find the parent
    repo instead of the test fixture's repo.
    """
    for key in _GIT_DISCOVERY_VARS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Bare workspace directory (no git repo)."""
    return tmp_path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialized git repo with one commit."""
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.name", "Test"], tmp_path)
    _run_git(["config", "user.email", "test@test.local"], tmp_path)
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


@pytest.fixture
def detached_head_repo(git_repo: Path) -> Path:
    """Git repo with HEAD detached at initial commit."""
    sha = _run_git_output(["rev-parse", "HEAD"], git_repo)
    _run_git(["checkout", sha], git_repo)
    return git_repo


@pytest.fixture
def merge_conflict_repo(git_repo: Path) -> Path:
    """Git repo with an active merge conflict."""
    _run_git(["checkout", "-b", "conflict-branch"], git_repo)
    (git_repo / "README.md").write_text("# Conflict branch\n")
    _run_git(["add", "."], git_repo)
    _run_git(["commit", "-m", "conflict change"], git_repo)
    _run_git(["switch", "-"], git_repo)
    (git_repo / "README.md").write_text("# Main change\n")
    _run_git(["add", "."], git_repo)
    _run_git(["commit", "-m", "main change"], git_repo)
    # Merge will fail with conflict — don't use check=True
    subprocess.run(
        ["git", "merge", "conflict-branch"],  # noqa: S607
        cwd=git_repo,
        capture_output=True,
        check=False,
        env=_GIT_ENV,
    )
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


@pytest.fixture
def allow_local_clone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow local file paths in clone URL validation for testing.

    Also sets ``GIT_ALLOW_PROTOCOL=file`` so that ``_run_git`` (which
    uses ``GIT_CONFIG_GLOBAL=/dev/null``) still permits the ``file``
    transport.
    """
    monkeypatch.setattr(
        git_tools_module,
        "_is_allowed_clone_url",
        lambda url: True,
    )
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")

"""Fixtures for integration tool tests."""

import os
import subprocess
from pathlib import Path  # noqa: TC003 — pytest evaluates annotations

import pytest

_GIT_ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@test.local",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@test.local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_PROTOCOL_FROM_USER": "0",
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
def git_repo(tmp_path: Path) -> Path:
    """Initialized git repo with one commit."""
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.name", "Test"], tmp_path)
    _run_git(["config", "user.email", "test@test.local"], tmp_path)
    (tmp_path / "README.md").write_text("# Test\n")
    _run_git(["add", "."], tmp_path)
    _run_git(["commit", "-m", "initial commit"], tmp_path)
    return tmp_path

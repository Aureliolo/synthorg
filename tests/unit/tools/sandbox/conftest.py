"""Fixtures for sandbox tests."""

from pathlib import Path

import pytest

from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox


@pytest.fixture
def sandbox_workspace(tmp_path: Path) -> Path:
    """Temporary workspace directory for sandbox tests."""
    return tmp_path


@pytest.fixture
def sandbox_config() -> SubprocessSandboxConfig:
    """Default sandbox configuration."""
    return SubprocessSandboxConfig()


@pytest.fixture
def subprocess_sandbox(
    sandbox_workspace: Path,
    sandbox_config: SubprocessSandboxConfig,
) -> SubprocessSandbox:
    """SubprocessSandbox instance with default config."""
    return SubprocessSandbox(
        config=sandbox_config,
        workspace=sandbox_workspace,
    )

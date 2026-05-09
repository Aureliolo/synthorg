"""Fixtures for sandbox tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from synthorg.tools.sandbox._image_resolution import (
    set_resolved_sandbox_image,
    set_resolved_sidecar_image,
)
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.subprocess_sandbox import SubprocessSandbox


@pytest.fixture(autouse=True)
def _isolate_sandbox_image_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Reset the resolved-image cache + env around each sandbox test.

    ``DockerSandboxConfig.image`` reads from a process-singleton cache
    populated by the lifecycle wiring. A test that pokes the cache via
    ``set_resolved_sandbox_image(...)`` could otherwise leak a value
    into later tests on the same xdist worker. We also clear the legacy
    env vars so a stray export in CI / a developer shell cannot reach
    the resolution path through a future regression.
    """
    monkeypatch.delenv("SYNTHORG_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("SYNTHORG_SIDECAR_IMAGE", raising=False)
    set_resolved_sandbox_image(None)
    set_resolved_sidecar_image(None)
    try:
        yield
    finally:
        set_resolved_sandbox_image(None)
        set_resolved_sidecar_image(None)


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

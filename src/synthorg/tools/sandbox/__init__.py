"""Sandbox backends for tool execution isolation."""

from .config import SubprocessSandboxConfig
from .docker_config import DockerSandboxConfig
from .docker_sandbox import DockerSandbox
from .errors import SandboxError, SandboxStartError, SandboxTimeoutError
from .factory import (
    build_sandbox_backends,
    cleanup_sandbox_backends,
    cleanup_tracked_sandbox_backends,
    resolve_sandbox_for_category,
)
from .protocol import SandboxBackend
from .result import SandboxResult
from .sandboxing_config import SandboxingConfig
from .subprocess_sandbox import SubprocessSandbox

__all__ = [
    "DockerSandbox",
    "DockerSandboxConfig",
    "SandboxBackend",
    "SandboxError",
    "SandboxResult",
    "SandboxStartError",
    "SandboxTimeoutError",
    "SandboxingConfig",
    "SubprocessSandbox",
    "SubprocessSandboxConfig",
    "build_sandbox_backends",
    "cleanup_sandbox_backends",
    "cleanup_tracked_sandbox_backends",
    "resolve_sandbox_for_category",
]

"""Sandbox backend protocol definition."""

from pathlib import (
    Path,  # noqa: TC003 — needed at runtime for @runtime_checkable Protocol
)
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.tools.sandbox.result import SandboxResult


@runtime_checkable
class SandboxBackend(Protocol):
    """Protocol for pluggable sandbox backends.

    Implementations execute commands in an isolated environment with
    environment filtering, workspace enforcement, and timeout support.
    Subprocess and Docker are built-in backends.
    """

    async def execute(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> SandboxResult:
        """Execute a command in the sandbox.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to sandbox workspace root).
            env_overrides: Extra environment variables for the sandbox.
            timeout: Seconds before the process is killed. Falls back
                to the backend's default timeout if ``None``.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the subprocess could not be started.
            SandboxError: If cwd is outside the workspace boundary.
        """
        ...

    async def cleanup(self) -> None:
        """Release any resources held by the backend.

        Returns:
            Nothing.
        """
        ...

    async def health_check(self) -> bool:
        """Return ``True`` if the backend is operational.

        Returns:
            ``True`` if healthy, ``False`` otherwise.
        """
        ...

    def get_backend_type(self) -> NotBlankStr:
        """Return a short identifier for this backend type.

        Returns:
            A string like ``'subprocess'`` or ``'docker'``.
        """
        ...

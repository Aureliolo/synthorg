"""Sandbox backend protocol definition."""

from pathlib import (
    Path,  # noqa: TC003 -- needed at runtime for @runtime_checkable Protocol
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

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: NotBlankStr | None = None,
    ) -> SandboxResult:
        """Execute a command in the sandbox.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to sandbox workspace root).
            env_overrides: Extra environment variables for the sandbox.
            timeout: Seconds before the process is killed. Falls back
                to the backend's default timeout if ``None``.
            owner_id: Lifecycle owner identifier (agent ID, task ID, or
                ``None`` for per-call semantics).  Must be non-blank
                when provided.  Used by the Docker backend's lifecycle
                strategy to decide container reuse.

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

    async def release_owner(self, owner_id: NotBlankStr) -> None:
        """Signal that *owner_id* no longer needs its sandbox resources.

        Wired at the owner boundary (task completion / agent stop).
        Backends with reusable containers (Docker) dispatch this to
        their lifecycle strategy (per-agent grace, per-task immediate
        destroy, per-call no-op).  Backends without per-owner resources
        (subprocess) treat it as a no-op.

        Args:
            owner_id: The same identifier passed as ``owner_id`` to
                ``execute`` (agent ID for per-agent, task ID for
                per-task).
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

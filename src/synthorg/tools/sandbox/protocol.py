"""Sandbox backend protocol definition."""

from collections.abc import Mapping
from pathlib import (
    Path,
)
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
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
        project_id: NotBlankStr | None = None,
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
            project_id: Owning project for per-execution per-project
                isolation.  The Docker backend rebinds
                ``<workspace>/projects/<project_id>`` at ``/workspace``
                and prefixes the lifecycle owner key so a container
                mounted for one project is never reused for another.
                ``None`` (or context-derived) selects the no-project
                whole-workspace mount.

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

    async def release_owner(
        self,
        owner_id: NotBlankStr,
        *,
        project_id: NotBlankStr | None = None,
        image_override: str | None = None,
    ) -> None:
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
            project_id: The project the owner ran under; the Docker
                backend prefixes the lifecycle key with it so the
                release matches the key ``execute`` acquired under.
            image_override: The reproducible-environment image the owner
                ran under; the Docker backend suffixes the lifecycle key
                with its identity so the release matches the key
                ``execute`` acquired under. ``None`` when no per-project
                environment applied.
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

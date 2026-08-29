"""Sandbox backend protocol definition."""

from collections.abc import Mapping
from pathlib import (
    Path,
)
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.persistence.background_job_protocol import BackgroundJobRecord
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
        category: str = "",
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
            category: The calling tool's :class:`ToolCategory` value. The
                Docker backend resolves both the container runtime and
                whether the workspace mount is writable from it, so a tool
                that omits its own silently takes the global default for
                both. Empty means no category, for the callers outside the
                tool layer that genuinely have none.
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

    async def start_background(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        category: str = "",
        owner_id: NotBlankStr,
        project_id: NotBlankStr | None = None,
    ) -> NotBlankStr:
        """Start *command* detached; the caller polls/reads/cancels it later.

        The job's lifetime is tied to the sandbox container that owns
        *owner_id*'s workspace: starting one pins that container open
        past what its lifecycle strategy's own grace/idle timers would
        otherwise allow, until the job reaches a terminal status.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to sandbox workspace root).
            env_overrides: Extra environment variables for the sandbox.
            category: The calling tool's :class:`ToolCategory` value.
            owner_id: Lifecycle owner identifier. Background jobs have
                no per-call semantics, so this is required (unlike
                ``execute``'s optional ``owner_id``).
            project_id: Owning project; see ``execute``.

        Returns:
            The started job's id, addressed by every other
            ``*_background`` method.

        Raises:
            SandboxBackgroundUnsupportedError: This backend has no
                persistent container a background job could run in.
            SandboxBackgroundNoReusableContainerError: The resolved
                lifecycle strategy destroys its container after every
                call, so there is nothing to pin.
            SandboxBackgroundJobLimitError: *owner_id* already holds
                the maximum number of live background jobs.
            SandboxStartError: The job could not be confirmed started.
        """
        ...

    async def poll_background(self, job_id: NotBlankStr) -> BackgroundJobRecord:
        """Return the current tracking row for *job_id*.

        A still-running job is polled directly (via the container) so
        the returned status reflects reality even if this is the first
        check since the job finished; a terminal job returns the
        persisted row unchanged.

        Args:
            job_id: The job to check.

        Returns:
            The job's current tracking row.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*.
        """
        ...

    async def read_background_output(
        self, job_id: NotBlankStr, *, byte_cap: int
    ) -> str:
        """Return *job_id*'s captured output, truncated to *byte_cap* bytes.

        Args:
            job_id: The job whose output to read.
            byte_cap: Maximum bytes to return.

        Returns:
            The captured stdout+stderr, interleaved as written, kept
            from the start (never the tail) when larger than the cap.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*.
        """
        ...

    async def cancel_background(self, job_id: NotBlankStr) -> BackgroundJobRecord:
        """Terminate *job_id*'s process group and mark it cancelled.

        A job that already reached a terminal status is left alone and
        returned as-is: cancelling a finished job is not an error.

        Args:
            job_id: The job to cancel.

        Returns:
            The job's tracking row after cancellation.

        Raises:
            SandboxBackgroundJobNotFoundError: No job matches *job_id*.
        """
        ...

    async def list_background_jobs(
        self, owner_id: NotBlankStr
    ) -> tuple[BackgroundJobRecord, ...]:
        """List background jobs recorded against *owner_id*, newest-first.

        Args:
            owner_id: Lifecycle owner to list jobs for.

        Returns:
            This owner's job rows, newest-first.
        """
        ...

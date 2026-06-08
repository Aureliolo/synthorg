"""Subprocess-based sandbox backend.

Executes commands via ``asyncio.create_subprocess_exec`` with
environment filtering, workspace boundary enforcement, timeout
management, and PATH restriction.
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.sandbox import (
    SANDBOX_CLEANUP,
    SANDBOX_EXECUTE_FAILED,
    SANDBOX_EXECUTE_START,
    SANDBOX_EXECUTE_SUCCESS,
    SANDBOX_EXECUTE_TIMEOUT,
    SANDBOX_HEALTH_CHECK,
    SANDBOX_KILL_FAILED,
    SANDBOX_WORKSPACE_VIOLATION,
)
from synthorg.tools.sandbox._subprocess_env import _EnvFilterMixin
from synthorg.tools.sandbox._subprocess_proc import (
    _close_process,
    _kill_process,
    _redact_args,
    _redact_credentials,
    _spawn_process,
)
from synthorg.tools.sandbox.active_environment import get_active_sandbox_environment
from synthorg.tools.sandbox.config import SubprocessSandboxConfig
from synthorg.tools.sandbox.errors import (
    SandboxError,
)
from synthorg.tools.sandbox.result import SandboxResult

logger = get_logger(__name__)

_DEFAULT_CONFIG = SubprocessSandboxConfig()
_DEFAULT_KILL_GRACE_SECONDS: Final[float] = 5.0
_PROJECTS_SUBDIR: Final[str] = "projects"
"""Fallback kill-grace used when no operator override is supplied.

Mirrors the ``tools.subprocess_kill_grace_timeout_seconds`` setting.
"""


class SubprocessSandbox(_EnvFilterMixin):
    """Subprocess sandbox backend.

    Runs commands in child processes with filtered environment variables,
    workspace boundary checks, and configurable timeouts.

    Attributes:
        config: Sandbox configuration.
        workspace: Absolute path to the workspace root directory.
    """

    def __init__(
        self,
        *,
        config: SubprocessSandboxConfig | None = None,
        workspace: Path,
        kill_grace_seconds: float = _DEFAULT_KILL_GRACE_SECONDS,
    ) -> None:
        """Initialize the subprocess sandbox.

        Args:
            config: Sandbox configuration (defaults to standard config).
            workspace: Absolute path to the workspace root. Must exist.
            kill_grace_seconds: Seconds to wait after ``proc.kill()``
                for the subprocess to flush pipes and terminate before
                giving up. Mirrors the
                ``tools.subprocess_kill_grace_timeout_seconds`` setting.
                Defaults to :data:`_DEFAULT_KILL_GRACE_SECONDS` so the
                sandbox still works standalone when the settings layer
                is not wired; the API startup hook resolves the setting
                and passes it in when constructing the sandbox. Must be
                positive.

        Raises:
            ValueError: If *workspace* is not absolute or does not
                exist, or if *kill_grace_seconds* is not positive.
        """
        if not workspace.is_absolute():
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                workspace=str(workspace),
                error="workspace must be an absolute path",
            )
            msg = f"workspace must be an absolute path, got: {workspace}"
            raise ValueError(msg)
        resolved = workspace.resolve()
        if not resolved.is_dir():
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                workspace=str(resolved),
                error="workspace directory does not exist",
            )
            msg = f"workspace directory does not exist: {resolved}"
            raise ValueError(msg)
        if kill_grace_seconds <= 0:
            msg = f"kill_grace_seconds must be > 0, got {kill_grace_seconds}"
            raise ValueError(msg)
        self._config = config or _DEFAULT_CONFIG
        self._workspace = resolved
        self._kill_grace_seconds = kill_grace_seconds

    @property
    def config(self) -> SubprocessSandboxConfig:
        """Sandbox configuration."""
        return self._config

    @property
    def workspace(self) -> Path:
        """Workspace root directory."""
        return self._workspace

    def _validate_cwd(self, cwd: Path) -> None:
        """Validate that *cwd* is within the workspace boundary.

        Args:
            cwd: Working directory to validate.

        Raises:
            SandboxError: If *cwd* is outside the workspace and
                ``workspace_only`` is enabled.
        """
        if not self._config.workspace_only:
            return
        try:
            cwd.resolve().relative_to(self._workspace)
        except ValueError as exc:
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                cwd=str(cwd),
                workspace=str(self._workspace),
            )
            msg = f"Working directory '{cwd}' is outside workspace '{self._workspace}'"
            raise SandboxError(msg) from exc

    def _project_root(self, project_id: NotBlankStr | None) -> Path:
        """Resolve the default working dir for *project_id*.

        ``None`` returns the workspace root; a set ``project_id`` returns
        ``<workspace>/projects/<project_id>`` (separator-guarded to block
        traversal out of the projects subtree).

        Rejects a missing project tree up front so a misprovisioned
        workspace surfaces as a sandbox error rather than an opaque
        command-spawn failure deep in ``create_subprocess_exec`` (and
        stays diagnosable in parity with the Docker backend).

        Returns:
            Result of type ``Path``.

        Raises:
            SandboxError: ``project_id`` bears path separators, or the
                project tree does not exist on disk.
        """
        if project_id is None:
            return self._workspace
        pid = str(project_id)
        if not pid.strip() or pid == "." or "/" in pid or "\\" in pid or ".." in pid:
            msg = f"refusing path-separator-bearing project_id {pid!r}"
            logger.warning(SANDBOX_WORKSPACE_VIOLATION, cwd=pid)
            raise SandboxError(msg)
        root = self._workspace / _PROJECTS_SUBDIR / pid
        try:
            exists = root.is_dir()
        except (OSError, ValueError) as exc:
            # An oversized / otherwise-invalid project_id can make the
            # stat raise (e.g. ENAMETOOLONG) rather than return False;
            # surface it as the same sandbox error instead of leaking a
            # raw OSError.
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                cwd=str(root),
                workspace=str(self._workspace),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"project workspace does not exist: {root}"
            raise SandboxError(msg) from exc
        if not exists:
            logger.warning(
                SANDBOX_WORKSPACE_VIOLATION,
                cwd=str(root),
                workspace=str(self._workspace),
            )
            msg = f"project workspace does not exist: {root}"
            raise SandboxError(msg)
        return root

    async def _communicate_with_timeout(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        args: tuple[str, ...],
        deadline: float,
    ) -> tuple[bytes, bytes, bool]:
        """Wait for process output with timeout handling.

        On timeout, kills the process and captures any partial output.

        Args:
            proc: The running subprocess.
            command: Command name (for logging).
            args: Command arguments (for logging).
            deadline: Seconds before kill.

        Returns:
            Tuple of (stdout_bytes, stderr_bytes, timed_out).
        """
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=deadline,
            )
        except TimeoutError:
            _kill_process(proc)
            stdout_bytes, stderr_bytes = await self._drain_after_kill(
                proc,
                command,
                args,
            )
            logger.warning(
                SANDBOX_EXECUTE_TIMEOUT,
                command=command,
                args=_redact_args(args),
                timeout=deadline,
            )
            return stdout_bytes, stderr_bytes, True
        return stdout_bytes, stderr_bytes, False

    async def _drain_after_kill(
        self,
        proc: asyncio.subprocess.Process,
        command: str,
        args: tuple[str, ...],
    ) -> tuple[bytes, bytes]:
        """Drain remaining output after killing a process.

        Waits up to ``self._kill_grace_seconds`` (the operator-tuned
        ``tools.subprocess_kill_grace_timeout_seconds`` setting, with
        :data:`_DEFAULT_KILL_GRACE_SECONDS` as the fallback) for the
        process to terminate. If it does not, logs an error and
        returns empty stdout with a diagnostic stderr message that
        reports the actual grace period used.

        Returns:
            Tuple ``(bytes, bytes)``.
        """
        grace = self._kill_grace_seconds
        try:
            return await asyncio.wait_for(
                proc.communicate(),
                timeout=grace,
            )
        except TimeoutError:
            logger.warning(
                SANDBOX_KILL_FAILED,
                command=command,
                args=_redact_args(args),
                pid=proc.pid,
                error=f"process did not terminate {grace}s after kill",
            )
            return b"", (
                f"[sandbox] process did not terminate after {grace}s kill grace"
            ).encode()

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        owner_id: str | None = None,  # noqa: ARG002
        project_id: NotBlankStr | None = None,
    ) -> SandboxResult:
        """Execute a command in the sandbox.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to the project subtree when
                *project_id* is set, else the workspace root).
            env_overrides: Extra env vars applied on top of filtered env.
            timeout: Seconds before the process is killed.
            owner_id: Lifecycle owner (ignored by subprocess backend).
            project_id: Owning project; when set and *cwd* is ``None``,
                the working dir defaults to
                ``<workspace>/projects/<project_id>``. The subprocess
                backend has no container mount, so this scopes the
                working directory rather than the filesystem view.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the subprocess could not be started.
            SandboxError: If cwd is outside the workspace boundary or
                if no safe PATH directories can be determined.
        """
        work_dir = cwd if cwd is not None else self._project_root(project_id)
        self._validate_cwd(work_dir)

        effective_timeout = (
            timeout if timeout is not None else self._config.timeout_seconds
        )
        # Per-task reproducible environment (ambient, set by the worker):
        # toolchain / PATH additions for this run. The declaration is
        # committed code, but it is broader than the trusted internal
        # overrides (git hardening vars), so its additions are screened
        # through the same denylist as the inherited host env before
        # merging; a declared secret-pattern var is dropped and logged.
        # An injected PATH is still re-filtered by ``_build_filtered_env``.
        # Explicit ``env_overrides`` stay trusted and win on conflict.
        # ``image_override`` has no meaning for subprocess.
        active_env = get_active_sandbox_environment()
        effective_overrides: Mapping[str, str] | None = env_overrides
        if active_env is not None and active_env.env_additions:
            screened = self._screen_declaration_env(active_env.env_additions)
            effective_overrides = {**screened, **(env_overrides or {})}
        env = self._build_filtered_env(effective_overrides)

        logger.debug(
            SANDBOX_EXECUTE_START,
            command=command,
            args=_redact_args(args),
            cwd=str(work_dir),
            timeout=effective_timeout,
        )

        proc = await _spawn_process(command, args, work_dir, env)
        try:
            (
                stdout_bytes,
                stderr_bytes,
                timed_out,
            ) = await self._communicate_with_timeout(
                proc,
                command,
                args,
                effective_timeout,
            )
        finally:
            _close_process(proc)

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        returncode = proc.returncode if proc.returncode is not None else -1

        if timed_out:
            return SandboxResult(
                stdout=stdout,
                stderr=(stderr or f"Process timed out after {effective_timeout}s"),
                returncode=returncode,
                timed_out=True,
            )

        if returncode != 0:
            logger.warning(
                SANDBOX_EXECUTE_FAILED,
                command=command,
                args=_redact_args(args),
                returncode=returncode,
                stderr=_redact_credentials(stderr),
            )
        else:
            logger.debug(
                SANDBOX_EXECUTE_SUCCESS,
                command=command,
                args=_redact_args(args),
            )

        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )

    async def cleanup(self) -> None:
        """Subprocesses are ephemeral -- no resources to release."""
        logger.debug(SANDBOX_CLEANUP, backend="subprocess")

    async def release_owner(
        self,
        owner_id: NotBlankStr,
        *,
        project_id: NotBlankStr | None = None,
        image_override: str | None = None,
    ) -> None:
        """No-op -- subprocesses hold no per-owner resources."""

    async def health_check(self) -> bool:
        """Return ``True`` if the workspace directory exists.

        Returns:
            ``True`` if the workspace directory exists, else ``False``.
        """
        healthy = self._workspace.is_dir()
        logger.debug(
            SANDBOX_HEALTH_CHECK,
            backend="subprocess",
            healthy=healthy,
            workspace=str(self._workspace),
        )
        return healthy

    def get_backend_type(self) -> NotBlankStr:
        """Return ``'subprocess'``.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("subprocess")

"""Adapt a sandbox backend to the environment command-runner seam.

Environment provisioning runs its setup commands through the
:class:`~synthorg.engine.workspace.environment.protocol.EnvironmentCommandRunner`
seam so the environment subsystem stays decoupled from the tool layer.
This adapter binds the resolved sandbox backend (and the owning project,
so the Docker backend mounts the right ``projects/<project_id>`` subtree)
to that seam, running each setup command inside the same sandbox the
agent's tools use.
"""

from collections.abc import Mapping
from pathlib import Path

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment.protocol import CommandOutcome
from synthorg.tools.sandbox.protocol import SandboxBackend


class SandboxEnvironmentRunner:
    """Runs environment setup commands through a sandbox backend."""

    __slots__ = ("_backend", "_project_id")

    def __init__(
        self,
        *,
        backend: SandboxBackend,
        project_id: NotBlankStr,
    ) -> None:
        self._backend = backend
        self._project_id = project_id

    async def run(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- matches runner protocol
    ) -> CommandOutcome:
        """Execute a setup command in the project's sandbox.

        Returns:
            The ``CommandOutcome`` from running the command in the sandbox.
        """
        result = await self._backend.execute(
            command=command,
            args=args,
            cwd=cwd,
            env_overrides=env,
            timeout=timeout,
            project_id=self._project_id,
        )
        return CommandOutcome(
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


__all__ = ["SandboxEnvironmentRunner"]

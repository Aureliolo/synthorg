"""Pluggable per-project environment strategy protocol + result models.

A :class:`EnvironmentStrategy` reads a declaration committed in the
project workspace (a bootstrap manifest, a ``devcontainer.json``, or a
``flake.nix``), and provisions a reproducible environment from it.  The
:class:`~synthorg.core.enums.EnvironmentType` discriminator selects one
of three strategies; the safe default is ``MANIFEST`` (backend-agnostic,
runs in both the subprocess and Docker sandboxes).

Bootstrap strategies (manifest / nix) run their setup into the mounted
workspace through an injected :class:`EnvironmentCommandRunner` (the
resolved sandbox backend, adapted), so the same declaration provisions
identically in the sandbox, in CI, and on a fresh clone.  The
devcontainer strategy builds a sealed image instead, returning its
reference in :attr:`ProvisionedEnvironment.image_ref`.
"""

from collections.abc import Mapping  # noqa: TC003 -- runtime annotation (PEP 649)
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.enums import EnvironmentType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class CommandOutcome(BaseModel):
    """Immutable result of a single setup-command execution.

    Attributes:
        command: The command line that was run (for logs / error context).
        exit_code: Process exit status.
        stdout: Captured standard output.
        stderr: Captured standard error.
        success: Computed -- ``True`` when ``exit_code`` is 0.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def success(self) -> bool:
        """Whether the command exited cleanly."""
        return self.exit_code == 0


@runtime_checkable
class EnvironmentCommandRunner(Protocol):
    """Runs a setup command in the project workspace.

    The environment subsystem never imports a sandbox backend directly;
    the service adapts the resolved
    :class:`~synthorg.tools.sandbox.protocol.SandboxBackend` to this
    narrow seam so bootstrap strategies stay decoupled from the tool
    layer (and unit tests inject a fake).
    """

    async def run(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 -- mirrors SandboxBackend.execute
    ) -> CommandOutcome:
        """Execute *command* with *args* in *cwd* and capture its outcome."""
        ...


class ScaffoldResult(BaseModel):
    """Outcome of a scaffold attempt.

    Attributes:
        files_written: Workspace-relative paths newly written by the
            scaffold (empty when a declaration already existed).
        seeded: ``True`` when a fresh declaration was written, ``False``
            when one already existed (scaffold is idempotent / no-op).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    files_written: tuple[str, ...] = ()
    seeded: bool = False


class ProvisionedEnvironment(BaseModel):
    """Result of provisioning a project environment.

    Attributes:
        environment_type: The declaration format that provisioned it.
        declaration_hash: Content hash of the declaration (cache key).
        image_ref: Built image reference (devcontainer image path only);
            ``None`` for the bootstrap (manifest / nix) paths.
        env_vars: Toolchain / PATH additions the sandbox should apply on
            subsequent tool calls (bootstrap paths).
        setup_log: Concatenated setup-command output, for diagnostics.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    environment_type: EnvironmentType
    declaration_hash: NotBlankStr
    image_ref: NotBlankStr | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    setup_log: str = ""


@runtime_checkable
class EnvironmentStrategy(Protocol):
    """Pluggable per-project reproducible-environment strategy."""

    def kind(self) -> EnvironmentType:
        """Return this strategy's discriminator."""
        ...

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if this strategy's declaration is present."""
        ...

    async def scaffold(self, workspace_path: Path) -> ScaffoldResult:
        """Write the default declaration if absent (idempotent no-op otherwise)."""
        ...

    def declaration_hash(self, workspace_path: Path) -> NotBlankStr:
        """Return a stable content hash over the declaration files.

        Covers exactly the files that affect the build (the declaration
        plus any lockfiles it references), so an unchanged declaration
        short-circuits re-provision and any change invalidates the cache.
        """
        ...

    def managed_paths(self, workspace_path: Path) -> tuple[str, ...]:
        """Workspace-relative paths this strategy authors or generates.

        The service stages and commits exactly these (the declaration
        plus any generated artifact such as ``bootstrap.sh``) so a fresh
        clone receives them, without sweeping unrelated agent edits into
        the environment commit.  Only paths that exist are returned.
        """
        ...

    def runtime_env_vars(self, workspace_path: Path) -> Mapping[str, str]:
        """Toolchain / PATH additions to apply to sandbox tool calls.

        Derived from the declaration on every call (a cheap re-read), so
        the active environment is available identically on a fresh
        provision and on cache reuse without persisting env state.
        Returns an empty mapping when the declaration carries none.
        """
        ...

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
    ) -> ProvisionedEnvironment:
        """Provision the environment from the declaration.

        Args:
            project_id: Owning project (image tagging, logs).
            workspace_path: The project's working tree.
            runner: Command runner for the bootstrap paths (manifest /
                nix run their setup through it).
            sandbox_kind: The backend resolved for the build/test tool
                categories (``"subprocess"`` / ``"docker"``); the
                devcontainer strategy needs Docker and raises
                :class:`EnvironmentBackendUnavailableError` otherwise.

        Raises:
            EnvironmentProvisionError: Setup failed.
            EnvironmentBackendUnavailableError: The declaration needs a
                backend that is not active.
        """
        ...

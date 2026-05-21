"""Nix-flake environment strategy.

Reads a committed ``flake.nix`` and provisions the hermetic dev shell it
declares by building it (``nix develop --command true``).  Building the
declared ``devShell`` IS provisioning the environment in nix's model:
the strategy delivers exactly what the flake promises (a reproducible
shell that exists and is buildable).

Boundary: this strategy provisions (builds) the declared shell; it does
not yet wrap every subsequent tool call in ``nix develop`` (threading the
nix store PATH into the sandbox is a separable downstream concern, and
nix store paths are host-absolute so they cannot be forwarded into a
container by value).  ``env_vars`` is therefore empty.
"""

import asyncio
import hashlib
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError, EnvironmentProvisionError
from synthorg.engine.workspace.environment.protocol import (
    EnvironmentCommandRunner,
    ProvisionedEnvironment,
    ScaffoldResult,
)
from synthorg.engine.workspace.environment.templates import DEFAULT_FLAKE_NIX
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_DECLARATION_SCAFFOLDED,
    ENVIRONMENT_PROVISION_FAILED,
    ENVIRONMENT_PROVISION_START,
    ENVIRONMENT_PROVISIONED,
)

logger = get_logger(__name__)

_FLAKE_FILENAME: Final[str] = "flake.nix"
_FLAKE_LOCK_FILENAME: Final[str] = "flake.lock"
_NIX: Final[str] = "nix"
_BUILD_SHELL_ARGS: Final[tuple[str, ...]] = ("develop", "--command", "true")


class NixEnvironmentStrategy:
    """Nix-flake hermetic dev-shell strategy."""

    def __init__(
        self,
        *,
        provision_timeout_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        self._provision_timeout = provision_timeout_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()

    def kind(self) -> EnvironmentType:
        """Return the ``NIX`` discriminator."""
        return EnvironmentType.NIX

    def _flake_path(self, workspace_path: Path) -> Path:
        return workspace_path / _FLAKE_FILENAME

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if a ``flake.nix`` is present."""
        return self._flake_path(workspace_path).is_file()

    async def scaffold(self, workspace_path: Path) -> ScaffoldResult:
        """Write the default flake if absent (idempotent no-op otherwise)."""
        if self.detect(workspace_path):
            return ScaffoldResult(seeded=False)
        await asyncio.to_thread(
            self._flake_path(workspace_path).write_text,
            DEFAULT_FLAKE_NIX,
            encoding="utf-8",
        )
        logger.info(
            ENVIRONMENT_DECLARATION_SCAFFOLDED,
            backend=EnvironmentType.NIX.value,
            filename=_FLAKE_FILENAME,
        )
        return ScaffoldResult(files_written=(_FLAKE_FILENAME,), seeded=True)

    def declaration_hash(self, workspace_path: Path) -> NotBlankStr:
        """SHA-256 over ``flake.nix`` plus ``flake.lock`` (if present)."""
        flake = self._flake_path(workspace_path)
        if not flake.is_file():
            msg = f"nix flake {_FLAKE_FILENAME!r} not found"
            raise EnvironmentConfigError(msg)
        digest = hashlib.sha256()
        digest.update(flake.read_bytes())
        lock = workspace_path / _FLAKE_LOCK_FILENAME
        if lock.is_file():
            digest.update(lock.read_bytes())
        return NotBlankStr(digest.hexdigest())

    def managed_paths(self, workspace_path: Path) -> tuple[str, ...]:
        """``flake.nix`` plus ``flake.lock`` (whichever exist)."""
        candidates = (_FLAKE_FILENAME, _FLAKE_LOCK_FILENAME)
        return tuple(c for c in candidates if (workspace_path / c).is_file())

    def runtime_env_vars(self, workspace_path: Path) -> dict[str, str]:
        """No forwarded env vars (see the tool-wrapping boundary above)."""
        del workspace_path
        return {}

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
    ) -> ProvisionedEnvironment:
        """Build the declared dev shell (``nix develop --command true``)."""
        del sandbox_kind  # nix runs through the runner in either backend
        if not self.detect(workspace_path):
            msg = f"nix flake {_FLAKE_FILENAME!r} not found"
            raise EnvironmentConfigError(msg)
        logger.info(
            ENVIRONMENT_PROVISION_START,
            project_id=str(project_id),
            backend=EnvironmentType.NIX.value,
        )
        outcome = await runner.run(
            command=_NIX,
            args=_BUILD_SHELL_ARGS,
            cwd=workspace_path,
            timeout=self._provision_timeout,
        )
        if not outcome.success:
            logger.error(
                ENVIRONMENT_PROVISION_FAILED,
                project_id=str(project_id),
                backend=EnvironmentType.NIX.value,
                exit_code=outcome.exit_code,
            )
            msg = "nix dev shell failed to build"
            raise EnvironmentProvisionError(msg)
        declaration_hash = self.declaration_hash(workspace_path)
        logger.info(
            ENVIRONMENT_PROVISIONED,
            project_id=str(project_id),
            backend=EnvironmentType.NIX.value,
        )
        return ProvisionedEnvironment(
            environment_type=EnvironmentType.NIX,
            declaration_hash=declaration_hash,
            image_ref=None,
            setup_log=f"{outcome.stdout}{outcome.stderr}",
        )


__all__ = ["NixEnvironmentStrategy"]

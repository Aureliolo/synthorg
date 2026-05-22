"""Bootstrap-manifest environment strategy (the safe default).

Reads a committed ``synthorg.env.yaml`` declaring the project's language,
lockfiles, ordered setup commands, and test command.  Provisioning runs
the setup commands into the mounted workspace through the injected
:class:`~synthorg.engine.workspace.environment.protocol.EnvironmentCommandRunner`
(so it works in both the subprocess and Docker sandboxes) and emits a
stock ``bootstrap.sh`` derived from the manifest, so a fresh clone is
reproducible with no SynthOrg present.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import EnvironmentType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError, EnvironmentProvisionError
from synthorg.engine.workspace.environment.protocol import (
    EnvironmentCommandRunner,
    ProvisionedEnvironment,
    ScaffoldResult,
)
from synthorg.engine.workspace.environment.templates import DEFAULT_MANIFEST_YAML
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_DECLARATION_SCAFFOLDED,
    ENVIRONMENT_LOCKFILE_PATH_REJECTED,
    ENVIRONMENT_PROVISION_FAILED,
    ENVIRONMENT_PROVISION_START,
    ENVIRONMENT_PROVISIONED,
)

logger = get_logger(__name__)

BOOTSTRAP_SCRIPT_NAME: Final[str] = "bootstrap.sh"
_SHELL: Final[str] = "sh"
# Bound the failed-command output captured in the structured error log so
# a noisy build cannot blow up the logging pipeline.
_MAX_ERROR_OUTPUT_CHARS: Final[int] = 2000


class EnvironmentManifest(BaseModel):
    """The committed bootstrap-manifest declaration.

    Attributes:
        language: Primary language of the deliverable (metadata).
        lockfiles: Version-pinning files hashed into the cache key.
        setup_commands: Ordered shell commands that install the toolchain
            and dependencies into the working tree.
        test_command: How a fresh clone runs the project's tests.
        env: Toolchain / PATH additions applied to later tool calls.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    language: NotBlankStr
    lockfiles: tuple[str, ...] = ()
    setup_commands: tuple[str, ...] = ()
    test_command: NotBlankStr
    env: dict[str, str] = Field(default_factory=dict)


class ManifestEnvironmentStrategy:
    """Backend-agnostic bootstrap-manifest strategy (default)."""

    def __init__(
        self,
        *,
        manifest_filename: str,
        provision_timeout_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        self._manifest_filename = manifest_filename
        self._provision_timeout = provision_timeout_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()

    def kind(self) -> EnvironmentType:
        """Return the ``MANIFEST`` discriminator."""
        return EnvironmentType.MANIFEST

    def _manifest_path(self, workspace_path: Path) -> Path:
        return workspace_path / self._manifest_filename

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if the manifest file is present."""
        return self._manifest_path(workspace_path).is_file()

    def _read_manifest(self, workspace_path: Path) -> EnvironmentManifest:
        path = self._manifest_path(workspace_path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            msg = f"failed to read environment manifest {self._manifest_filename!r}"
            raise EnvironmentConfigError(msg) from exc
        if not isinstance(raw, dict):
            msg = f"environment manifest {self._manifest_filename!r} must be a mapping"
            raise EnvironmentConfigError(msg)
        try:
            return EnvironmentManifest.model_validate(raw)
        except ValidationError as exc:
            msg = f"invalid environment manifest {self._manifest_filename!r}"
            raise EnvironmentConfigError(msg) from exc

    async def scaffold(self, workspace_path: Path) -> ScaffoldResult:
        """Write the default manifest if absent (idempotent no-op otherwise)."""
        if self.detect(workspace_path):
            return ScaffoldResult(seeded=False)
        path = self._manifest_path(workspace_path)
        await asyncio.to_thread(
            path.write_text, DEFAULT_MANIFEST_YAML, encoding="utf-8"
        )
        logger.info(
            ENVIRONMENT_DECLARATION_SCAFFOLDED,
            backend=EnvironmentType.MANIFEST.value,
            filename=self._manifest_filename,
        )
        return ScaffoldResult(files_written=(self._manifest_filename,), seeded=True)

    def declaration_hash(self, workspace_path: Path) -> NotBlankStr:
        """SHA-256 over the manifest plus its listed lockfiles."""
        path = self._manifest_path(workspace_path)
        if not path.is_file():
            msg = f"environment manifest {self._manifest_filename!r} not found"
            raise EnvironmentConfigError(msg)
        digest = hashlib.sha256()
        digest.update(path.read_bytes())
        manifest = self._read_manifest(workspace_path)
        for lockfile in manifest.lockfiles:
            digest.update(lockfile.encode("utf-8"))
            lock_path = self._resolve_lockfile(workspace_path, lockfile)
            if lock_path is not None and lock_path.is_file():
                digest.update(lock_path.read_bytes())
        return NotBlankStr(digest.hexdigest())

    def _resolve_lockfile(self, workspace_path: Path, lockfile: str) -> Path | None:
        """Resolve a declared lockfile, rejecting any workspace escape.

        A lockfile path is project-authored; an absolute path or a
        ``..`` traversal would let the declaration hash arbitrary files
        outside the working tree. Rejected paths are logged and skipped
        (the path string still feeds the hash, so editing it re-provisions).
        """
        candidate = Path(lockfile)
        root = workspace_path.resolve()
        resolved = (workspace_path / candidate).resolve()
        if candidate.is_absolute() or not resolved.is_relative_to(root):
            logger.warning(
                ENVIRONMENT_LOCKFILE_PATH_REJECTED,
                backend=EnvironmentType.MANIFEST.value,
                lockfile=lockfile,
            )
            return None
        return resolved

    def managed_paths(self, workspace_path: Path) -> tuple[str, ...]:
        """The manifest plus the generated ``bootstrap.sh`` (if present)."""
        candidates = (self._manifest_filename, BOOTSTRAP_SCRIPT_NAME)
        return tuple(c for c in candidates if (workspace_path / c).is_file())

    def runtime_env_vars(self, workspace_path: Path) -> dict[str, str]:
        """The manifest's declared ``env`` additions (empty if absent)."""
        if not self.detect(workspace_path):
            return {}
        return dict(self._read_manifest(workspace_path).env)

    def _render_bootstrap(self, manifest: EnvironmentManifest) -> str:
        lines = [
            "#!/usr/bin/env sh",
            f"# Generated by SynthOrg from {self._manifest_filename}. "
            "Do not edit by hand.",
            "# A fresh clone runs this to build the environment reproducibly.",
            f"# Run tests with: {manifest.test_command}",
            "set -eu",
            "",
        ]
        lines.extend(manifest.setup_commands or [":"])
        return "\n".join(lines) + "\n"

    async def _write_bootstrap(
        self, workspace_path: Path, manifest: EnvironmentManifest
    ) -> None:
        script = self._render_bootstrap(manifest)
        await asyncio.to_thread(
            (workspace_path / BOOTSTRAP_SCRIPT_NAME).write_text,
            script,
            encoding="utf-8",
        )

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
    ) -> ProvisionedEnvironment:
        """Run the manifest's setup commands and emit ``bootstrap.sh``."""
        del sandbox_kind  # manifest bootstrap runs in any backend
        manifest = self._read_manifest(workspace_path)
        logger.info(
            ENVIRONMENT_PROVISION_START,
            project_id=str(project_id),
            backend=EnvironmentType.MANIFEST.value,
            command_count=len(manifest.setup_commands),
        )
        await self._write_bootstrap(workspace_path, manifest)
        logs: list[str] = []
        for command in manifest.setup_commands:
            outcome = await runner.run(
                command=_SHELL,
                args=("-c", command),
                cwd=workspace_path,
                env=manifest.env or None,
                timeout=self._provision_timeout,
            )
            logs.append(f"$ {command}\n{outcome.stdout}{outcome.stderr}")
            if not outcome.success:
                output_tail = (outcome.stdout + outcome.stderr)[
                    -_MAX_ERROR_OUTPUT_CHARS:
                ]
                logger.error(
                    ENVIRONMENT_PROVISION_FAILED,
                    project_id=str(project_id),
                    backend=EnvironmentType.MANIFEST.value,
                    command=command,
                    exit_code=outcome.exit_code,
                    output_tail=output_tail,
                )
                msg = (
                    f"environment setup command failed (exit {outcome.exit_code}): "
                    f"{command!r}"
                )
                raise EnvironmentProvisionError(msg)
        declaration_hash = self.declaration_hash(workspace_path)
        logger.info(
            ENVIRONMENT_PROVISIONED,
            project_id=str(project_id),
            backend=EnvironmentType.MANIFEST.value,
        )
        return ProvisionedEnvironment(
            environment_type=EnvironmentType.MANIFEST,
            declaration_hash=declaration_hash,
            image_ref=None,
            env_vars=dict(manifest.env),
            setup_log="\n".join(logs),
        )


__all__ = [
    "BOOTSTRAP_SCRIPT_NAME",
    "EnvironmentManifest",
    "ManifestEnvironmentStrategy",
]

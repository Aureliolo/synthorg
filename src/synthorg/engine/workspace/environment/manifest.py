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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.criterion_match import criterion_key
from synthorg.core.project_enums import EnvironmentType
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


class PendingTest(BaseModel):
    """One acceptance criterion, and the test that will decide it.

    Both halves are named here because the manifest is the single authority on
    what is pending. Matching a criterion to a test by reading the test's own
    name would give the name runtime meaning, and a rename nobody thought was
    load-bearing would then silently un-pend a criterion.

    Attributes:
        criterion: The criterion key, normalised through :func:`criterion_key`
            so it survives a re-spelling of the objective it came from.
        test_id: The runner's node id for the test asserting that criterion,
            matched against the machine-readable report.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr
    test_id: NotBlankStr


class DependencyPolicy(BaseModel):
    """What the project may and may not depend on.

    Stated as two lists rather than one, because "nothing outside this set" and
    "anything except this set" are different claims and a project needs to be
    able to make either. An empty ``allowed`` means no allowlist is in force,
    which is not the same as allowing nothing.

    Attributes:
        allowed: Package names admitted. Empty means no allowlist applies.
        denied: Package names refused, checked whether or not an allowlist is
            in force, so a denial cannot be undone by widening the allowlist.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allowed: tuple[str, ...] = ()
    denied: tuple[str, ...] = ()


class EnvironmentManifest(BaseModel):
    """The committed bootstrap-manifest declaration, and the project's gates.

    This file is both halves of the skeleton's contract with the units below
    it: how a fresh clone is brought up, and what "done" means once it is. A
    definition of done with nowhere to live is a definition of done nobody
    enforces, so it lives here, committed, beside the commands that produce the
    thing it judges.

    Attributes:
        language: Primary language of the deliverable (metadata).
        lockfiles: Version-pinning files hashed into the cache key.
        setup_commands: Ordered shell commands that install the toolchain
            and dependencies into the working tree, and the command that boots
            the result.
        test_command: How a fresh clone runs the project's tests.
        env: Toolchain / PATH additions applied to later tool calls.
        lint_command: How a fresh clone lints. Absent means no lint gate.
        format_command: How a fresh clone checks formatting. Absent means no
            formatting gate.
        coverage_floor: The minimum coverage fraction a run must reach. Absent
            means no floor, which is not the same as a floor of zero: a floor
            of zero is a declared decision and is reported as met.
        dependency_policy: What the project may depend on.
        test_report_path: Where the test runner writes machine-readable
            per-test results, relative to the workspace root. Absent means the
            runner reports only an exit status, which is enough to say a run
            failed and not enough to say a pending test failed for its declared
            reason, so a project with pending criteria needs one.
        pending: The criteria whose tests are declared pending, each paired
            with the test that will decide it. A unit clears its own entry in
            the commit that makes its test pass, and that removal is the
            readiness signal, so this field is mutable committed state on
            purpose.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    language: NotBlankStr
    lockfiles: tuple[str, ...] = ()
    setup_commands: tuple[str, ...] = ()
    test_command: NotBlankStr
    env: dict[str, str] = Field(default_factory=dict)
    lint_command: NotBlankStr | None = None
    format_command: NotBlankStr | None = None
    coverage_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    dependency_policy: DependencyPolicy = Field(default_factory=DependencyPolicy)
    test_report_path: str | None = None
    pending: tuple[PendingTest, ...] = ()

    @model_validator(mode="after")
    def _validate_pending(self) -> EnvironmentManifest:
        """Reject a pending set that cannot be matched or cannot be classified.

        Returns:
            The validated manifest.

        Raises:
            ValueError: When a criterion is not already normalised, when one
                criterion or one test is claimed twice, or when pending
                criteria are declared with no report to classify them from.
        """
        criteria: set[str] = set()
        tests: set[str] = set()
        for entry in self.pending:
            key = criterion_key(entry.criterion)
            if key != entry.criterion:
                msg = (
                    f"pending criterion {entry.criterion!r} is not normalised; "
                    f"expected {key!r}"
                )
                raise ValueError(msg)
            if key in criteria:
                msg = f"pending criterion {key!r} is declared twice"
                raise ValueError(msg)
            # Two criteria sharing one test cannot both be cleared
            # independently, so the second unit to finish would find its marker
            # already gone and read as done without having run.
            if entry.test_id in tests:
                msg = f"pending test {entry.test_id!r} is claimed by two criteria"
                raise ValueError(msg)
            criteria.add(key)
            tests.add(entry.test_id)
        if self.pending and self.test_report_path is None:
            msg = (
                "pending criteria need test_report_path: an exit status cannot "
                "separate a declared assertion failure from a collection error, "
                "so every pending test would have to classify red"
            )
            raise ValueError(msg)
        return self


def read_manifest(workspace_path: Path, *, filename: str) -> EnvironmentManifest:
    """Read and validate the committed manifest under *workspace_path*.

    Module-level so the strategy that provisions from the manifest and the
    capture path that reads its pending set share one reader: two would let a
    field the strategy accepts be one the capture path silently ignores.

    Raises:
        EnvironmentConfigError: The file is unreadable, is not a mapping, or
            does not validate.

    Returns:
        The parsed manifest.
    """
    path = workspace_path / filename
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        msg = f"failed to read environment manifest {filename!r}"
        raise EnvironmentConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"environment manifest {filename!r} must be a mapping"
        raise EnvironmentConfigError(msg)
    try:
        return EnvironmentManifest.model_validate(raw)
    except ValidationError as exc:
        msg = f"invalid environment manifest {filename!r}"
        raise EnvironmentConfigError(msg) from exc


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
        """Return the ``MANIFEST`` discriminator.

        Returns:
            :attr:`EnvironmentType.MANIFEST`.
        """
        return EnvironmentType.MANIFEST

    def _manifest_path(self, workspace_path: Path) -> Path:
        return workspace_path / self._manifest_filename

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if the manifest file is present.

        Returns:
            ``True`` when the configured manifest file exists in the
            workspace; ``False`` otherwise.
        """
        return self._manifest_path(workspace_path).is_file()

    def _read_manifest(self, workspace_path: Path) -> EnvironmentManifest:
        return read_manifest(workspace_path, filename=self._manifest_filename)

    async def scaffold(self, workspace_path: Path) -> ScaffoldResult:
        """Write the default manifest if absent (idempotent no-op otherwise).

        Returns:
            A :class:`ScaffoldResult` with ``seeded=True`` and the
            written filename when a fresh manifest was created;
            ``seeded=False`` when one already existed.
        """
        if await asyncio.to_thread(self.detect, workspace_path):
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
        """SHA-256 over the manifest plus its listed lockfiles.

        Returns:
            The lowercase hex SHA-256 digest covering the manifest
            bytes and (when listed) the resolved lockfile bytes.

        Raises:
            EnvironmentConfigError: When the manifest file is absent.
        """
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

        Returns:
            The resolved absolute path to ``lockfile`` when it lives
            under the workspace root; ``None`` when the path is
            absolute or escapes the workspace.
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
        """The manifest plus the generated ``bootstrap.sh`` (if present).

        Returns:
            Workspace-relative path strings for the manifest and the
            generated bootstrap script, in declaration order; paths
            that are not present are omitted.
        """
        candidates = (self._manifest_filename, BOOTSTRAP_SCRIPT_NAME)
        return tuple(c for c in candidates if (workspace_path / c).is_file())

    def runtime_env_vars(self, workspace_path: Path) -> dict[str, str]:
        """The manifest's declared ``env`` additions (empty if absent).

        Returns:
            Mapping of env-var name to value from the manifest's
            ``env`` field; ``{}`` when no manifest is present.
        """
        if not self.detect(workspace_path):
            return {}
        return dict(self._read_manifest(workspace_path).env)

    def _render_bootstrap(self, manifest: EnvironmentManifest) -> str:
        lines = [
            "#!/usr/bin/env sh",
            (
                f"# Generated by SynthOrg from {self._manifest_filename}. "
                "Do not edit by hand."
            ),
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
        """Run the manifest's setup commands and emit ``bootstrap.sh``.

        Returns:
            A :class:`ProvisionedEnvironment` carrying the manifest
            declaration hash, ``image_ref=None`` (manifest backends
            do not pin an image), the manifest's env vars, and the
            captured setup log.

        Raises:
            EnvironmentProvisionError: When any setup command exits
                non-zero (the command and exit code are logged before
                raising).
        """
        del sandbox_kind  # manifest bootstrap runs in any backend
        manifest = await asyncio.to_thread(self._read_manifest, workspace_path)
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
        declaration_hash = await asyncio.to_thread(
            self.declaration_hash, workspace_path
        )
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

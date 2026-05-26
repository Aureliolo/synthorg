"""Devcontainer environment strategy.

Reads a committed ``.devcontainer/devcontainer.json``.  An ``image:``
declaration is used as-is; a ``build:`` declaration builds a sealed image
from the referenced Dockerfile (tagged deterministically by declaration
hash).  Any ``postCreateCommand`` runs through the injected runner to
populate the mounted workspace.

Requires the Docker sandbox backend: a sealed image is the whole point of
devcontainer, so on a project whose build/test categories resolve to the
subprocess backend this raises
:class:`~synthorg.engine.errors.EnvironmentBackendUnavailableError` rather
than silently degrading to an unfaithful host-only run.

Contract: the resulting image runs under the existing sandbox host config
(read-only root, ``CapDrop: ALL``, ``no-new-privileges``), so a
devcontainer image must be able to run its commands writing only to the
mounted ``/workspace`` and ``/tmp``.
"""

import asyncio
import hashlib
import json
import re
from pathlib import Path  # noqa: TC003 -- runtime annotation (PEP 649)
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import EnvironmentType
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    EnvironmentBackendUnavailableError,
    EnvironmentConfigError,
    EnvironmentDockerBuildError,
    EnvironmentProvisionError,
)
from synthorg.engine.workspace.environment.protocol import (
    EnvironmentCommandRunner,
    ProvisionedEnvironment,
    ScaffoldResult,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.environment.image_builder import (
        BuildOutcome,
        ImageBuilder,
    )
from synthorg.engine.workspace.environment.templates import DEFAULT_DEVCONTAINER_JSON
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import (
    ENVIRONMENT_DECLARATION_SCAFFOLDED,
    ENVIRONMENT_IMAGE_BUILD_COMPLETE,
    ENVIRONMENT_IMAGE_BUILD_FAILED,
    ENVIRONMENT_IMAGE_BUILD_RETRY,
    ENVIRONMENT_IMAGE_BUILD_START,
    ENVIRONMENT_PROVISION_FAILED,
    ENVIRONMENT_PROVISION_START,
    ENVIRONMENT_PROVISIONED,
)

logger = get_logger(__name__)

_DEVCONTAINER_DIR: Final[str] = ".devcontainer"
_DEVCONTAINER_JSON: Final[str] = "devcontainer.json"
_DOCKER_BACKEND: Final[str] = "docker"
_SHELL: Final[str] = "sh"
_TAG_HASH_LEN: Final[int] = 12
_TAG_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9_.-]+")
# Lowercased build-log markers that indicate a transient (retryable)
# image build failure: registry / network / daemon hiccups, not a
# deterministic Dockerfile error.
_TRANSIENT_BUILD_MARKERS: Final[tuple[str, ...]] = (
    "timeout",
    "timed out",
    "connection refused",
    "connection reset",
    "i/o timeout",
    "tls handshake",
    "temporary failure",
    "temporarily unavailable",
    "503 service",
    "too many requests",
    "no such host",
    "dial tcp",
    "eof",
)


class _TransientBuildError(EnvironmentDockerBuildError):
    """Internal retry signal for a transient image-build failure.

    Subclasses :class:`EnvironmentDockerBuildError` so it stays within the
    domain-error hierarchy; it is an internal control-flow signal that the
    retry handler consumes and that never escapes the strategy (a transient
    failure exhausting the retry budget is re-raised as a plain
    :class:`EnvironmentDockerBuildError`).
    """

    def __init__(self, outcome: BuildOutcome) -> None:
        super().__init__(f"transient image build failure for {outcome.tag!r}")
        self.outcome = outcome


def _is_transient_build_failure(outcome: BuildOutcome) -> bool:
    """Classify a failed build as transient (retryable) or deterministic.

    A timeout is always transient; otherwise the combined build log is
    scanned for registry / network / daemon markers. A plain non-zero
    exit with no transient marker is a deterministic Dockerfile failure
    and is not retried.

    Returns:
        ``True`` when the build outcome looks transient (timeout or a
        known registry/network/daemon marker in the log) and should
        be retried; ``False`` for deterministic Dockerfile failures.
    """
    if outcome.timed_out:
        return True
    haystack = outcome.log.lower()
    return any(marker in haystack for marker in _TRANSIENT_BUILD_MARKERS)


class DevcontainerEnvironmentStrategy:
    """Devcontainer (sealed-image) strategy; Docker backend only."""

    def __init__(  # noqa: PLR0913 -- image builder + build-retry tuning is the boundary surface
        self,
        *,
        image_builder: ImageBuilder,
        docker_build_timeout_seconds: float,
        build_max_attempts: int,
        build_retry_base_seconds: float,
        build_retry_cap_seconds: float,
        clock: Clock | None = None,
    ) -> None:
        self._image_builder = image_builder
        self._build_timeout = docker_build_timeout_seconds
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._build_retry = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, _TransientBuildError),
            max_attempts=build_max_attempts,
            base=build_retry_base_seconds,
            cap=build_retry_cap_seconds,
            event=ENVIRONMENT_IMAGE_BUILD_RETRY,
            clock=self._clock,
        )

    def kind(self) -> EnvironmentType:
        """Return the ``DEVCONTAINER`` discriminator."""
        return EnvironmentType.DEVCONTAINER

    def _nested_path(self, workspace_path: Path) -> Path:
        return workspace_path / _DEVCONTAINER_DIR / _DEVCONTAINER_JSON

    def _declaration_path(self, workspace_path: Path) -> Path:
        """Return the existing declaration path, or the scaffold target."""
        nested = self._nested_path(workspace_path)
        if nested.is_file():
            return nested
        flat = workspace_path / f"{_DEVCONTAINER_DIR}.json"
        if flat.is_file():
            return flat
        return nested

    def detect(self, workspace_path: Path) -> bool:
        """Return ``True`` if a devcontainer declaration is present."""
        return self._declaration_path(workspace_path).is_file()

    async def scaffold(self, workspace_path: Path) -> ScaffoldResult:
        """Write the default declaration if absent (idempotent no-op otherwise).

        Returns:
            A :class:`ScaffoldResult` with ``seeded=True`` and the
            written file path when a fresh declaration was created;
            ``seeded=False`` when one already existed.
        """
        if self.detect(workspace_path):
            return ScaffoldResult(seeded=False)
        target = self._nested_path(workspace_path)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            target.write_text, DEFAULT_DEVCONTAINER_JSON, encoding="utf-8"
        )
        rel = f"{_DEVCONTAINER_DIR}/{_DEVCONTAINER_JSON}"
        logger.info(
            ENVIRONMENT_DECLARATION_SCAFFOLDED,
            backend=EnvironmentType.DEVCONTAINER.value,
            filename=rel,
        )
        return ScaffoldResult(files_written=(rel,), seeded=True)

    def _read_config(self, workspace_path: Path) -> dict[str, object]:
        path = self._declaration_path(workspace_path)
        if not path.is_file():
            msg = f"devcontainer declaration {_DEVCONTAINER_JSON!r} not found"
            raise EnvironmentConfigError(msg)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            msg = f"failed to read devcontainer declaration {_DEVCONTAINER_JSON!r}"
            raise EnvironmentConfigError(msg) from exc
        if not isinstance(raw, dict):
            msg = "devcontainer declaration must be a JSON object"
            raise EnvironmentConfigError(msg)
        return raw

    def declaration_hash(self, workspace_path: Path) -> NotBlankStr:
        """SHA-256 over the declaration plus its referenced Dockerfile.

        Returns:
            The lowercase hex SHA-256 digest covering the declaration
            bytes and (when a ``build:`` declaration is used) the
            referenced Dockerfile.

        Raises:
            EnvironmentConfigError: When the declaration file is missing
                or unreadable as JSON.
        """
        path = self._declaration_path(workspace_path)
        if not path.is_file():
            msg = f"devcontainer declaration {_DEVCONTAINER_JSON!r} not found"
            raise EnvironmentConfigError(msg)
        # Length-frame each input so different (declaration, Dockerfile)
        # byte splits can never collapse to the same combined stream and
        # reuse a stale provisioning cache entry.
        digest = hashlib.sha256()
        decl_bytes = path.read_bytes()
        digest.update(f"{_DEVCONTAINER_JSON}:{len(decl_bytes)}:".encode())
        digest.update(decl_bytes)
        config = self._read_config(workspace_path)
        build = config.get("build")
        if isinstance(build, dict):
            dockerfile = self._resolve_within(
                workspace_path, path.parent, build.get("dockerfile", "Dockerfile")
            )
            if dockerfile is not None and dockerfile.is_file():
                df_bytes = dockerfile.read_bytes()
                digest.update(f"dockerfile:{len(df_bytes)}:".encode())
                digest.update(df_bytes)
        return NotBlankStr(digest.hexdigest())

    def managed_paths(self, workspace_path: Path) -> tuple[str, ...]:
        """The devcontainer declaration plus its referenced Dockerfile.

        Returns:
            Workspace-relative paths managed by this strategy in
            POSIX form; ``()`` when no declaration is present.
        """
        path = self._declaration_path(workspace_path)
        if not path.is_file():
            return ()
        paths = [path.relative_to(workspace_path).as_posix()]
        config = self._read_config(workspace_path)
        build = config.get("build")
        if isinstance(build, dict):
            dockerfile = self._resolve_within(
                workspace_path, path.parent, build.get("dockerfile", "Dockerfile")
            )
            if dockerfile is not None and dockerfile.is_file():
                paths.append(dockerfile.relative_to(workspace_path).as_posix())
        return tuple(paths)

    def runtime_env_vars(self, workspace_path: Path) -> dict[str, str]:
        """The declaration's ``containerEnv`` additions (empty if absent).

        Returns:
            Mapping of environment variable name to string value from
            the declaration's ``containerEnv``; ``{}`` when the field
            is missing or not a JSON object.
        """
        if not self.detect(workspace_path):
            return {}
        container_env = self._read_config(workspace_path).get("containerEnv")
        if not isinstance(container_env, dict):
            return {}
        return {str(k): str(v) for k, v in container_env.items()}

    def _resolve_within(
        self, workspace_path: Path, base_dir: Path, candidate: object
    ) -> Path | None:
        """Resolve *candidate* under *base_dir*, rejecting workspace escape.

        Returns:
            The resolved absolute :class:`Path` when ``candidate`` is
            a non-empty string under the workspace; ``None`` when the
            input is unusable (non-string, empty).

        Raises:
            EnvironmentConfigError: When the resolved path escapes the
                workspace root.
        """
        if not isinstance(candidate, str) or not candidate:
            return None
        resolved = (base_dir / candidate).resolve()
        root = workspace_path.resolve()
        if not resolved.is_relative_to(root):
            msg = f"devcontainer path {candidate!r} escapes the workspace"
            raise EnvironmentConfigError(msg)
        return resolved

    def _image_tag(self, project_id: NotBlankStr, declaration_hash: str) -> NotBlankStr:
        slug = _TAG_UNSAFE.sub("-", str(project_id).lower()).strip("-") or "project"
        short = declaration_hash[:_TAG_HASH_LEN]
        return NotBlankStr(f"synthorg-project-{slug}:{short}")

    async def _build_image(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        build: dict[str, object],
        declaration_hash: str,
    ) -> NotBlankStr:
        decl_dir = self._declaration_path(workspace_path).parent
        dockerfile = self._resolve_within(
            workspace_path, decl_dir, build.get("dockerfile", "Dockerfile")
        )
        if dockerfile is None or not dockerfile.is_file():
            msg = "devcontainer build declares no readable Dockerfile"
            raise EnvironmentConfigError(msg)
        context = (
            self._resolve_within(workspace_path, decl_dir, build.get("context"))
            or decl_dir
        )
        tag = self._image_tag(project_id, declaration_hash)
        logger.info(
            ENVIRONMENT_IMAGE_BUILD_START, project_id=str(project_id), tag=str(tag)
        )

        async def _attempt() -> BuildOutcome:
            built = await self._image_builder.build(
                tag=tag,
                dockerfile=dockerfile,
                context_dir=context,
                timeout=self._build_timeout,
            )
            if not built.success and _is_transient_build_failure(built):
                raise _TransientBuildError(built)
            return built

        try:
            outcome = await self._build_retry.execute(
                _attempt, project_id=str(project_id), tag=str(tag)
            )
        except _TransientBuildError as exc:
            # Transient failure that exhausted the retry budget.
            logger.error(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                project_id=str(project_id),
                tag=str(tag),
                exit_code=exc.outcome.exit_code,
                reason="transient_exhausted",
            )
            msg = f"devcontainer image build failed for {tag!r}"
            raise EnvironmentDockerBuildError(msg) from exc
        if not outcome.success:
            logger.error(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                project_id=str(project_id),
                tag=str(tag),
                exit_code=outcome.exit_code,
            )
            msg = f"devcontainer image build failed for {tag!r}"
            raise EnvironmentDockerBuildError(msg)
        logger.info(
            ENVIRONMENT_IMAGE_BUILD_COMPLETE, project_id=str(project_id), tag=str(tag)
        )
        return tag

    def _resolve_image_ref(
        self,
        *,
        config: dict[str, object],
    ) -> NotBlankStr:
        image = config.get("image")
        if not isinstance(image, str) or not image:
            msg = "devcontainer declaration needs an 'image' or 'build'"
            raise EnvironmentConfigError(msg)
        return NotBlankStr(image)

    async def _run_post_create(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        config: dict[str, object],
        runner: EnvironmentCommandRunner,
    ) -> str:
        post_create = config.get("postCreateCommand")
        if post_create is None:
            return ""
        command: str
        args: tuple[str, ...]
        if isinstance(post_create, str):
            command, args = _SHELL, ("-c", post_create)
        elif isinstance(post_create, list) and all(
            isinstance(part, str) for part in post_create
        ):
            if not post_create:
                msg = "devcontainer postCreateCommand list must not be empty"
                raise EnvironmentConfigError(msg)
            parts: list[str] = [str(p) for p in post_create]
            command, args = parts[0], tuple(parts[1:])
        else:
            msg = "devcontainer postCreateCommand must be a string or string list"
            raise EnvironmentConfigError(msg)
        outcome = await runner.run(
            command=command,
            args=args,
            cwd=workspace_path,
            timeout=self._build_timeout,
        )
        if not outcome.success:
            logger.error(
                ENVIRONMENT_PROVISION_FAILED,
                project_id=str(project_id),
                backend=EnvironmentType.DEVCONTAINER.value,
                exit_code=outcome.exit_code,
            )
            msg = "devcontainer postCreateCommand failed"
            raise EnvironmentProvisionError(msg)
        return f"{outcome.stdout}{outcome.stderr}"

    async def provision(
        self,
        *,
        project_id: NotBlankStr,
        workspace_path: Path,
        runner: EnvironmentCommandRunner,
        sandbox_kind: NotBlankStr,
    ) -> ProvisionedEnvironment:
        """Build/select the image and run any postCreateCommand.

        Returns:
            A :class:`ProvisionedEnvironment` carrying the declaration
            hash, the resolved image reference, the merged container
            env vars, and the post-create setup log.

        Raises:
            EnvironmentBackendUnavailableError: When the sandbox backend
                is not Docker (devcontainer requires the sealed-image
                Docker path; a host subprocess backend would silently
                degrade and is rejected).
        """
        if str(sandbox_kind) != _DOCKER_BACKEND:
            msg = (
                "devcontainer environment requires the Docker sandbox backend; "
                f"build/test categories resolve to {sandbox_kind!r}"
            )
            raise EnvironmentBackendUnavailableError(msg)
        config = self._read_config(workspace_path)
        declaration_hash = self.declaration_hash(workspace_path)
        logger.info(
            ENVIRONMENT_PROVISION_START,
            project_id=str(project_id),
            backend=EnvironmentType.DEVCONTAINER.value,
        )
        build = config.get("build")
        if isinstance(build, dict):
            image_ref = await self._build_image(
                project_id=project_id,
                workspace_path=workspace_path,
                build=build,
                declaration_hash=str(declaration_hash),
            )
        else:
            image_ref = self._resolve_image_ref(config=config)
        setup_log = await self._run_post_create(
            project_id=project_id,
            workspace_path=workspace_path,
            config=config,
            runner=runner,
        )
        container_env = config.get("containerEnv")
        env_vars = (
            {str(k): str(v) for k, v in container_env.items()}
            if isinstance(container_env, dict)
            else {}
        )
        logger.info(
            ENVIRONMENT_PROVISIONED,
            project_id=str(project_id),
            backend=EnvironmentType.DEVCONTAINER.value,
        )
        return ProvisionedEnvironment(
            environment_type=EnvironmentType.DEVCONTAINER,
            declaration_hash=declaration_hash,
            image_ref=image_ref,
            env_vars=env_vars,
            setup_log=setup_log,
        )


__all__ = ["DevcontainerEnvironmentStrategy"]

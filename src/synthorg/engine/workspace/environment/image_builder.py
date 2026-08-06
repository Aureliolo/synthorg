"""Docker image builder for the devcontainer strategy.

The devcontainer strategy builds a sealed image from a ``build`` /
Dockerfile declaration.  The build runs on the host daemon through
``aiodocker`` over the mounted ``docker.sock``, the same client every
other daemon call in the tree uses (the sandbox, the fine-tune runner,
telemetry), so the backend image ships no Docker CLI and the build
context is tarred in-process rather than by a subprocess.
"""

import asyncio
import io
import tarfile
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import aiodocker
from pydantic import BaseModel, ConfigDict, computed_field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import EnvironmentConfigError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_IMAGE_BUILD_FAILED

logger = get_logger(__name__)

#: Marks a rendered chunk the daemon reported as an error, so the classifier
#: reads one shape whether the entry carried ``error`` or ``errorDetail``.
_ERROR_PREFIX: Final[str] = "ERROR: "


def _render_chunk(chunk: object) -> str:
    """Render one daemon stream entry as a log line.

    Args:
        chunk: A decoded JSON entry from the build stream.

    Returns:
        The entry's ``stream`` text, or its error prefixed so the
        classifier can spot it without re-inspecting the JSON.
    """
    if not isinstance(chunk, Mapping):
        return str(chunk)
    error = chunk.get("error")
    if error:
        return f"{_ERROR_PREFIX}{error}"
    stream = chunk.get("stream")
    return str(stream) if stream else ""


class BuildFailure(StrEnum):
    """Why a build did not produce an image.

    Distinct members rather than a shared numeric sentinel: an
    unreachable daemon, a build the caller cut short, and a Dockerfile
    the daemon rejected want different operator responses, and one code
    meaning all three tells the reader nothing.

    Attributes:
        DAEMON_UNAVAILABLE: The daemon could not be reached at all, so
            no build ever started.
        TIMED_OUT: The build ran past the caller's ceiling and was cut.
        BUILD_FAILED: The daemon ran the build and it failed.
    """

    DAEMON_UNAVAILABLE = "daemon_unavailable"
    TIMED_OUT = "timed_out"
    BUILD_FAILED = "build_failed"


class BuildOutcome(BaseModel):
    """Immutable result of a docker image build.

    Attributes:
        tag: The image tag the build targeted.
        failure: Why the build produced no image, or ``None`` on success.
        log: Combined build output, for diagnostics.
        success: Computed -- ``True`` when there is no failure.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tag: NotBlankStr
    failure: BuildFailure | None = None
    log: str = ""

    @computed_field
    @property
    def success(self) -> bool:
        """Whether the build produced the image."""
        return self.failure is None


@runtime_checkable
class ImageBuilder(Protocol):
    """Builds a Docker image from a Dockerfile and context directory."""

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Build *dockerfile* in *context_dir*, tagged *tag*."""
        ...


class AiodockerImageBuilder:
    """Builds images on the host daemon through ``aiodocker``."""

    @staticmethod
    def _assert_contained(dockerfile: Path, context_dir: Path) -> Path:
        """Reject a Dockerfile that escapes the build context.

        The daemon requires the Dockerfile to live inside the build
        context, and a caller-supplied path that resolves (through
        symlinks) outside ``context_dir`` would let an attacker have a
        build read arbitrary host files. Both paths are fully resolved
        before the containment check so a symlink cannot slip the
        Dockerfile out of the context after validation.

        Returns:
            The Dockerfile's context-relative path, which is what the
            daemon is given.

        Raises:
            EnvironmentConfigError: When ``dockerfile`` does not resolve
                to a path inside ``context_dir`` (422).
        """
        resolved_context = context_dir.resolve()
        resolved_dockerfile = dockerfile.resolve()
        if not resolved_dockerfile.is_relative_to(resolved_context):
            msg = (
                f"Dockerfile {resolved_dockerfile} is outside the build "
                f"context {resolved_context}"
            )
            raise EnvironmentConfigError(msg)
        return resolved_dockerfile.relative_to(resolved_context)

    @staticmethod
    def _context_tar(context_dir: Path) -> io.BytesIO:
        """Pack *context_dir* into an in-memory gzip tar for the daemon.

        The daemon takes the build context as a tar stream, which the
        CLI would otherwise assemble. Symlinks are archived as symlinks
        rather than followed, so a link pointing outside the context
        arrives as a dangling link inside the build rather than as a
        copy of whatever it targeted.

        Returns:
            A rewound gzip-tar stream of the context directory.
        """
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            archive.add(str(context_dir), arcname=".", recursive=True)
        buffer.seek(0)
        return buffer

    @staticmethod
    async def _connect() -> aiodocker.Docker:
        """Open a client and confirm the daemon answers.

        Returns:
            A connected client the caller must close.

        Raises:
            aiodocker.DockerError: Propagated when the daemon is
                unreachable; the caller maps it to
                ``DAEMON_UNAVAILABLE``.
        """
        client = aiodocker.Docker()
        try:
            await client.version()
        except BaseException:
            await client.close()
            raise
        return client

    async def build(
        self,
        *,
        tag: NotBlankStr,
        dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Build the image and capture the daemon's build log.

        Returns:
            A :class:`BuildOutcome` carrying the tag, the combined log,
            and the :class:`BuildFailure` when no image was produced.

        Raises:
            EnvironmentConfigError: When ``dockerfile`` resolves outside
                ``context_dir`` (path-containment guard, before connect).
            CancelledError: Propagated after the client is closed, which
                drops the build connection so the daemon is not left
                streaming into nothing.
        """
        relative_dockerfile = self._assert_contained(dockerfile, context_dir)
        try:
            client = await self._connect()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; the
            # daemon can refuse in a dozen aiodocker/aiohttp/OS shapes, and
            # every one of them means the same thing to the caller.
            reraise_critical(exc)
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.DAEMON_UNAVAILABLE.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return BuildOutcome(
                tag=tag,
                failure=BuildFailure.DAEMON_UNAVAILABLE,
                log=safe_error_description(exc),
            )
        try:
            return await self._build_with(
                client,
                tag=tag,
                relative_dockerfile=relative_dockerfile,
                context_dir=context_dir,
                timeout=timeout,
            )
        finally:
            # Shielded so an outer cancellation cannot unwind before the
            # connection is dropped, which is what tells the daemon to stop
            # streaming a build nobody is reading.
            await asyncio.shield(client.close())

    async def _build_with(
        self,
        client: aiodocker.Docker,
        *,
        tag: NotBlankStr,
        relative_dockerfile: Path,
        context_dir: Path,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Run one build on *client* and classify what came back.

        Returns:
            The :class:`BuildOutcome` for this build.
        """
        context = await asyncio.to_thread(self._context_tar, context_dir)
        try:
            log = await asyncio.wait_for(
                self._consume(
                    client.images.build(
                        fileobj=context,
                        encoding="gzip",
                        path_dockerfile=relative_dockerfile.as_posix(),
                        tag=str(tag),
                        rm=True,
                        stream=True,
                    )
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.TIMED_OUT.value,
                timeout_seconds=timeout,
            )
            return BuildOutcome(tag=tag, failure=BuildFailure.TIMED_OUT)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; a
            # build that dies mid-stream is reported to the caller as a
            # failed build rather than escaping as a 500 from provisioning.
            reraise_critical(exc)
            detail = safe_error_description(exc)
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.BUILD_FAILED.value,
                error_type=type(exc).__name__,
                error=detail,
            )
            return BuildOutcome(tag=tag, failure=BuildFailure.BUILD_FAILED, log=detail)
        return self._classify(tag, log)

    @staticmethod
    async def _consume(stream: Any) -> list[str]:
        """Drain the daemon's build stream into its log lines.

        The daemon reports a failed build as an ``error`` entry in the
        stream rather than by raising, so the entries are kept and
        classified by the caller.

        Returns:
            One entry per streamed chunk, in order.
        """
        return [_render_chunk(chunk) async for chunk in stream]

    @staticmethod
    def _classify(tag: NotBlankStr, lines: list[str]) -> BuildOutcome:
        """Turn drained build output into an outcome.

        Returns:
            A failed outcome when the daemon reported an error entry,
            else a successful one carrying the build log.
        """
        log = "".join(lines)
        failed = any(line.startswith(_ERROR_PREFIX) for line in lines)
        if failed:
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.BUILD_FAILED.value,
            )
            return BuildOutcome(tag=tag, failure=BuildFailure.BUILD_FAILED, log=log)
        return BuildOutcome(tag=tag, log=log)


__all__ = [
    "AiodockerImageBuilder",
    "BuildFailure",
    "BuildOutcome",
    "ImageBuilder",
]

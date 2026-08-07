"""Docker image builder for the devcontainer strategy.

The devcontainer strategy builds a sealed image from a ``build`` /
Dockerfile declaration.  The build runs on the host daemon through
``aiodocker`` over the mounted ``docker.sock``, the same client every
other daemon call in the tree uses (the sandbox, the fine-tune runner,
telemetry), so the backend image ships no Docker CLI and the build
context is tarred in-process rather than by a subprocess.

Assembling that context is the sibling :mod:`._context` module's job.
This one owns the daemon: the connection, the stream, the verdict, and
the teardown that stops a cancelled provision leaving a build running.
"""

import asyncio
from collections.abc import AsyncIterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final, NamedTuple, Protocol, runtime_checkable

import aiodocker
from pydantic import BaseModel, ConfigDict, computed_field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.environment._context import (
    CONTEXT_MAX_BYTES_KEY,
    ContextTooLargeError,
    ResolvedContext,
    assert_contained,
    context_tar,
)
from synthorg.engine.workspace.environment._dockerignore import load_dockerignore
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workspace import ENVIRONMENT_IMAGE_BUILD_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import registered_default_int
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Strong references to in-flight connection closes. A task with no
#: strong reference can be collected mid-flight, because asyncio holds
#: only a weak one, and a collected close leaves the daemon streaming a
#: build nobody is reading.
_PENDING_CLOSES: Final[set[asyncio.Task[None]]] = set()


def _chunk_error(chunk: object) -> str | None:
    """Return the daemon's error text for *chunk*, or ``None``.

    The daemon reports a failed build as a stream entry rather than by
    raising, under either ``error`` or the richer ``errorDetail``.
    Reading both matters: an ``errorDetail``-only entry is a real failed
    build, and missing it reports the build as a success.

    Args:
        chunk: A decoded JSON entry from the build stream.

    Returns:
        The error text when the entry reports one, else ``None``.
    """
    if not isinstance(chunk, Mapping):
        return None
    detail = chunk.get("errorDetail")
    if isinstance(detail, Mapping):
        message = detail.get("message")
        if message:
            return str(message)
    error = chunk.get("error")
    return str(error) if error else None


def _chunk_stream_text(chunk: object) -> str:
    """Render one non-error daemon stream entry as a log line.

    Returns:
        The entry's ``stream`` text, empty when it carries none.
    """
    if not isinstance(chunk, Mapping):
        return str(chunk)
    stream = chunk.get("stream")
    return str(stream) if stream else ""


class _BuildLog(NamedTuple):
    """What the daemon streamed for one build.

    The verdict is carried alongside the text rather than re-derived
    from it: a RUN step whose own stdout opens a line with ``ERROR:`` is
    not a failed build, and a rendered blob cannot tell the two apart.

    Attributes:
        lines: Every entry's text, in order.
        failed: Whether any entry was a daemon-reported error.
    """

    lines: tuple[str, ...]
    failed: bool


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
        CONTEXT_TOO_LARGE: The context exceeded the operator's ceiling,
            so nothing was uploaded. Deliberately not transient: a retry
            packs the same workspace.
    """

    DAEMON_UNAVAILABLE = "daemon_unavailable"
    TIMED_OUT = "timed_out"
    BUILD_FAILED = "build_failed"
    CONTEXT_TOO_LARGE = "context_too_large"


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
    """Builds images on the host daemon through ``aiodocker``.

    Args:
        config_resolver: Read per build for the context-size ceiling, so
            an operator raising it applies to the next build with no
            restart. Without one the registered default applies, which
            is the same value the resolver would return for an unset
            setting rather than a second opinion about it.
    """

    def __init__(self, config_resolver: ConfigResolverProtocol | None = None) -> None:
        self._config_resolver = config_resolver

    async def _context_ceiling(self) -> int:
        """Resolve the maximum context size this build may pack.

        Returns:
            The operator's ceiling in bytes.
        """
        namespace = SettingNamespace.COORDINATION.value
        if self._config_resolver is None:
            return registered_default_int(namespace, CONTEXT_MAX_BYTES_KEY)
        return await self._config_resolver.get_int(namespace, CONTEXT_MAX_BYTES_KEY)

    @staticmethod
    async def _connect() -> aiodocker.Docker:
        """Open a client and confirm the daemon answers.

        Returns:
            A connected client the caller must close.

        Raises:
            Exception: Whatever the transport raises when the daemon is
                unreachable, after the half-open client is closed. There
                is no single type to name: a missing socket is
                ``FileNotFoundError``, a refused one is
                ``ConnectionRefusedError``, a daemon that answers badly
                is ``aiodocker.DockerError``, and the caller maps all of
                them to ``DAEMON_UNAVAILABLE``.
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

        ``timeout`` bounds everything after the containment guard, the
        daemon handshake and the context pack included, so a hung socket
        or an enormous workspace cannot hold a provisioning run open
        past the ceiling its caller set.

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
        resolved = assert_contained(dockerfile, context_dir)
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            async with asyncio.timeout_at(deadline):
                client = await self._connect()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; the
            # daemon can refuse in a dozen aiodocker/aiohttp/OS shapes, and
            # every one of them means the same thing to the caller.
            # lint-allow: swallow-ok -- not swallowed: an unreachable daemon is
            # returned as a typed DAEMON_UNAVAILABLE outcome the provisioning
            # path handles by degrading, and raising would turn a handled
            # degradation into a 500 out of a greenlit run.
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
                resolved=resolved,
                deadline=deadline,
                timeout=timeout,
            )
        finally:
            await self._drop_connection(client, tag=tag)

    @staticmethod
    async def _drop_connection(client: aiodocker.Docker, *, tag: NotBlankStr) -> None:
        """Close *client* without losing the build's own outcome.

        Shielded so an outer cancellation cannot unwind before the
        connection is dropped, which is what tells the daemon to stop
        streaming a build nobody is reading. A close that fails is
        logged rather than raised: it runs in a ``finally``, so raising
        would replace the outcome the caller is waiting for with a
        teardown detail, turning a reported build failure into a 500.

        Raises:
            CancelledError: Propagated, leaving the close running.
        """
        closing = asyncio.ensure_future(client.close())
        _PENDING_CLOSES.add(closing)
        closing.add_done_callback(_forget_close)
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            # ``closing`` keeps running; _PENDING_CLOSES is the strong
            # reference that stops it being collected before it does.
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort teardown of a connection
            # already being discarded; the build's own verdict is the signal.
            reraise_critical(exc)
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                note="daemon connection close failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _build_with(
        self,
        client: aiodocker.Docker,
        *,
        tag: NotBlankStr,
        resolved: ResolvedContext,
        deadline: float,
        timeout: float,  # noqa: ASYNC109 -- caller-tuned build ceiling
    ) -> BuildOutcome:
        """Run one build on *client* and classify what came back.

        Returns:
            The :class:`BuildOutcome` for this build.
        """
        try:
            async with asyncio.timeout_at(deadline):
                log = await self._stream_build(client, tag=tag, resolved=resolved)
        except TimeoutError:
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.TIMED_OUT.value,
                timeout_seconds=timeout,
            )
            return BuildOutcome(tag=tag, failure=BuildFailure.TIMED_OUT)
        except ContextTooLargeError as exc:
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.CONTEXT_TOO_LARGE.value,
                packed_bytes=exc.packed_bytes,
                limit_bytes=exc.limit_bytes,
            )
            return BuildOutcome(
                tag=tag,
                failure=BuildFailure.CONTEXT_TOO_LARGE,
                log=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; a
            # build that dies mid-stream is reported to the caller as a
            # failed build rather than escaping as a 500 from provisioning.
            # lint-allow: swallow-ok -- not swallowed: the failure is returned
            # as a typed BUILD_FAILED outcome carrying the redacted log, which
            # is what the caller decides on.
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

    async def _stream_build(
        self,
        client: aiodocker.Docker,
        *,
        tag: NotBlankStr,
        resolved: ResolvedContext,
    ) -> _BuildLog:
        """Pack the context, hand it to the daemon, and drain the stream.

        Returns:
            Everything the daemon streamed, and whether it reported an
            error entry.
        """
        ignore = await asyncio.to_thread(
            load_dockerignore, resolved.context, resolved.context / resolved.dockerfile
        )
        limit_bytes = await self._context_ceiling()
        # Packing runs on a worker thread, which cancellation cannot
        # interrupt: an expired deadline abandons the result rather than
        # stopping the walk, and the ceiling is what bounds that walk.
        context = await asyncio.to_thread(context_tar, resolved, ignore, limit_bytes)
        return await self._consume(
            client.images.build(
                fileobj=context,
                encoding="gzip",
                path_dockerfile=resolved.dockerfile.as_posix(),
                tag=str(tag),
                rm=True,
                stream=True,
            )
        )

    @staticmethod
    async def _consume(stream: AsyncIterable[object]) -> _BuildLog:
        """Drain the daemon's build stream into its log and its verdict.

        Returns:
            One line per streamed chunk, in order, and whether any of
            them was a daemon-reported error.
        """
        lines: list[str] = []
        failed = False
        async for chunk in stream:
            error = _chunk_error(chunk)
            if error is not None:
                failed = True
                lines.append(error)
                continue
            lines.append(_chunk_stream_text(chunk))
        return _BuildLog(lines=tuple(lines), failed=failed)

    @staticmethod
    def _classify(tag: NotBlankStr, log: _BuildLog) -> BuildOutcome:
        """Turn drained build output into an outcome.

        Returns:
            A failed outcome when the daemon reported an error entry,
            else a successful one carrying the build log.
        """
        text = "".join(log.lines)
        if log.failed:
            logger.warning(
                ENVIRONMENT_IMAGE_BUILD_FAILED,
                tag=str(tag),
                reason=BuildFailure.BUILD_FAILED.value,
            )
            return BuildOutcome(tag=tag, failure=BuildFailure.BUILD_FAILED, log=text)
        return BuildOutcome(tag=tag, log=text)


def _forget_close(task: asyncio.Task[None]) -> None:
    """Drop a finished close, retrieving its failure so asyncio stays quiet.

    Args:
        task: The finished close task.
    """
    _PENDING_CLOSES.discard(task)
    if not task.cancelled():
        task.exception()


__all__ = [
    "AiodockerImageBuilder",
    "BuildFailure",
    "BuildOutcome",
    "ContextTooLargeError",
    "ImageBuilder",
]

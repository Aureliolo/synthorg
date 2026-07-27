# module-kind: adapter
"""Streaming one-shot container mixin for ``DockerSandbox``.

The exec model (:class:`DockerSandboxExecMixin`) runs a command in a
keep-alive container and returns its buffered output. The OpenHands loop
needs the opposite shape: a single long-lived process whose stdout is an
event stream the host consumes line-by-line so it can tear the run down
the instant a budget / shutdown / cancellation boundary trips.

This mixin adds that one interaction: create a dedicated container from
the configured image, attach to its stdin/stdout before start, write one
newline-terminated spec line to stdin, and yield each stdout line as it
arrives. Egress is pinned by the network sidecar whenever the backend's
``allowed_hosts`` is set, so the streamed process reaches only the
allowlisted hosts. The container and its sidecar are always torn down
when the iterator is exhausted, the consumer stops early, or an error
propagates.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import aiodocker
from aiodocker.stream import Stream
from aiodocker.types import JSONObject

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_EXEC_STREAM_TRUNCATED,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_TIMEOUT,
)
from synthorg.tools.sandbox.docker_config import (
    CONTAINER_WORKSPACE,
    DockerSandboxConfig,
)
from synthorg.tools.sandbox.errors import SandboxStartError
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle

logger = get_logger(__name__)

# aiodocker attach stream frame identifier for stderr (non-TTY multiplexed
# stream); every other frame id is treated as stdout.
_STREAM_STDERR: Final[int] = 2
# Cap a single streamed stdout line so a runaway process inside the
# container cannot exhaust host memory in the line buffer.
_MAX_LINE_CHARS: Final[int] = 1_000_000
# Cap buffered stderr in the structured log so binary container output
# cannot blow up the logging pipeline.
_MAX_STDERR_LOG_CHARS: Final[int] = 400


class DockerSandboxStreamMixin:
    """One-shot container spawn with a line-oriented stdout stream."""

    # Attributes + collaborator methods supplied by the concrete
    # DockerSandbox and its sibling mixins. Declared TYPE_CHECKING-only
    # so they never shadow the real runtime implementations in the MRO.
    if TYPE_CHECKING:
        _config: DockerSandboxConfig

        async def _ensure_docker(self) -> aiodocker.Docker:
            """Connect to the Docker daemon.

            Returns:
                An ``aiodocker.Docker`` client.
            """
            ...

        def _needs_sidecar(self) -> bool:
            """Whether network enforcement needs a sidecar.

            Returns:
                ``True`` when a sidecar must be brought up.
            """
            ...

        async def _bring_up_sidecar(self, docker: aiodocker.Docker) -> str:
            """Create, start, and health-check the network sidecar.

            Returns:
                The healthy sidecar container id.
            """
            ...

        async def _project_root(self, project_id: str | None) -> Path:
            """Resolve the per-execution mount root.

            Returns:
                The host path bound at ``/workspace``.
            """
            ...

        def _build_container_config(  # noqa: PLR0913
            self,
            *,
            command: str,
            args: tuple[str, ...],
            container_cwd: str,
            env_overrides: Mapping[str, str] | None,
            effective_root: Path | None = None,
            category: str = "",
            network_mode: str | None = None,
            owner_id: str | None = None,
            image_override: NotBlankStr | None = None,
        ) -> dict[str, object]:
            """Build the Docker container creation config.

            Returns:
                A config dict for ``aiodocker`` container creation.
            """
            ...

        async def _track_container(
            self, container_id: str, sidecar_id: str | None
        ) -> None:
            """Track a container for orphan reconciliation."""
            ...

        async def _untrack_container(self, container_id: str) -> None:
            """Drop a tracked container / sidecar-alias entry."""
            ...

        async def _destroy_handle(self, handle: ContainerHandle) -> None:
            """Stop + remove a container and its sidecar; untrack both."""
            ...

    async def stream_container_task(
        self,
        *,
        command: NotBlankStr,
        args: tuple[str, ...],
        stdin_line: str,
        idle_timeout_seconds: float,
        category: str = "",
        project_id: NotBlankStr | None = None,
    ) -> AsyncGenerator[str]:
        """Run ``command`` as a one-shot container, yielding its stdout lines.

        Creates a dedicated container, attaches to stdin/stdout, writes
        ``stdin_line`` (one newline-terminated spec), and yields each
        decoded stdout line. Sidecar egress enforcement is applied when
        the backend's ``allowed_hosts`` is set. The container and sidecar
        are always destroyed when iteration ends for any reason.

        Args:
            command: Executable to run (image entrypoint).
            args: Arguments to ``command``.
            stdin_line: One newline-terminated JSON spec fed to stdin.
            idle_timeout_seconds: Max seconds to wait for the next stdout
                frame before treating the run as hung.
            category: Tool category for runtime resolution.
            project_id: Owning project; selects the mounted workspace
                subtree. ``None`` mounts the whole workspace root.

        Yields:
            Each stdout line the container emits, newline stripped.

        Raises:
            SandboxStartError: If the container or sidecar cannot start.
            SandboxError: If ``project_id`` escapes the workspace root.
        """
        docker = await self._ensure_docker()
        effective_root = await self._project_root(
            str(project_id) if project_id is not None else None
        )
        handle: ContainerHandle | None = None
        stream: Stream | None = None
        try:
            # Spawn inside the try so a cancellation/failure at any step after
            # the container is created still reaches the teardown finally;
            # ``_spawn_stream_container`` additionally self-cleans on its own
            # post-create failure (the handle is not yet visible here).
            handle = await self._spawn_stream_container(
                docker,
                command=command,
                args=args,
                effective_root=effective_root,
                category=category,
            )
            stream = self._attach_stream(docker, handle.container_id)
            await self._start_container(docker, handle.container_id)
            await stream.write_in(stdin_line.encode("utf-8"))
            async for line in self._iter_lines(stream, idle_timeout_seconds):
                yield line
            # Natural EOF: surface an abnormal container exit for
            # diagnosability (the host maps a missing ``finished`` to ERROR).
            await self._log_exit_status(docker, handle.container_id)
        finally:
            # Nest so destroying the container/sidecar runs even if a second
            # cancellation lands while the stream close is awaiting: a bare
            # sequence of awaits here would let that cancellation skip the
            # handle teardown and leak the container.
            try:
                if stream is not None:
                    await self._close_stream(stream)
            finally:
                if handle is not None:
                    await self._destroy_handle(handle)

    async def _spawn_stream_container(
        self,
        docker: aiodocker.Docker,
        *,
        command: str,
        args: tuple[str, ...],
        effective_root: Path,
        category: str,
    ) -> ContainerHandle:
        """Create (but not start) the streaming container + its sidecar.

        Returns:
            A :class:`ContainerHandle` for the created container.

        Raises:
            SandboxStartError: If sidecar or container creation fails.
        """
        sidecar_id: str | None = None
        network_mode: str | None = None
        if self._needs_sidecar():
            sidecar_id = await self._bring_up_sidecar(docker)
            network_mode = f"container:{sidecar_id}"
        config = self._build_container_config(
            command=command,
            args=args,
            container_cwd=CONTAINER_WORKSPACE,
            env_overrides=None,
            effective_root=effective_root,
            category=category,
            network_mode=network_mode,
        )
        # Model X: attach to stdin/stdout before start so no output frame is
        # missed and the spec can be written to the process's stdin.
        config["OpenStdin"] = True
        config["AttachStdin"] = True
        config["StdinOnce"] = False
        config["Tty"] = False
        try:
            container = await docker.containers.create(cast("JSONObject", config))  # pyright: ignore[reportAttributeAccessIssue]
        except BaseException as exc:
            # BaseException (not just Exception): a cancellation while the
            # create is in flight must still tear down the already-running
            # sidecar, or it is orphaned untracked until shutdown cleanup.
            reraise_critical(exc)
            if sidecar_id is not None:
                await self._destroy_handle(
                    ContainerHandle(container_id=sidecar_id, sidecar_id=None)
                )
                # ``_bring_up_sidecar`` tracked the sidecar under its
                # ``_sidecar:*`` alias, but ``_destroy_handle`` only untracks the
                # raw container id; drop the alias too or it lingers in the
                # in-memory map (mirrors the success path below).
                await self._untrack_container(f"_sidecar:{sidecar_id}")
            if isinstance(exc, Exception):
                error_desc = safe_error_description(exc)
                logger.warning(
                    DOCKER_EXECUTE_FAILED,
                    error_type=type(exc).__name__,
                    error=error_desc,
                )
                msg = f"Failed to create streaming container: {error_desc}"
                raise SandboxStartError(msg) from exc
            raise
        handle = ContainerHandle(
            container_id=container.id,
            sidecar_id=sidecar_id,
            network_mode=network_mode or self._config.network,
        )
        # The sidecar is now folded into the container handle; drop its
        # standalone tracking alias so the tracked-container map does not retain
        # a dead ``_sidecar:*`` entry per streaming run (mirrors the exec path).
        if sidecar_id is not None:
            await self._untrack_container(f"_sidecar:{sidecar_id}")
        # Tracking is best-effort persistence, but a cancellation while it
        # awaits would strand the just-created container before the caller
        # can see the handle; destroy it on any failure (incl. cancellation).
        try:
            await self._track_container(container.id, sidecar_id)
        except BaseException:
            await self._destroy_handle(handle)
            raise
        return handle

    @staticmethod
    def _attach_stream(docker: aiodocker.Docker, container_id: str) -> Stream:
        """Attach to a container's multiplexed stdin/stdout/stderr stream.

        Returns:
            The attached ``aiodocker`` :class:`Stream`.
        """
        container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
        return container_obj.attach(stdin=True, stdout=True, stderr=True, logs=True)

    @staticmethod
    async def _start_container(docker: aiodocker.Docker, container_id: str) -> None:
        """Start the attached container.

        Raises:
            SandboxStartError: If the container fails to start.
        """
        container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
        try:
            await container_obj.start()
        except Exception as exc:
            reraise_critical(exc)
            error_desc = safe_error_description(exc)
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = f"Failed to start streaming container {container_id[:12]}"
            raise SandboxStartError(msg) from exc

    async def _iter_lines(
        self,
        stream: Stream,
        idle_timeout_seconds: float,
    ) -> AsyncIterator[str]:
        """Yield complete stdout lines from a multiplexed attach stream.

        stderr frames are logged (container diagnostics) but never yielded,
        and crucially do **not** extend the idle deadline: only real stdout
        progress resets it, so a hung container that keeps emitting stderr
        chatter still trips the timeout (and the host's boundary checks stay
        responsive) rather than being kept alive forever.

        Yields:
            Each newline-delimited stdout line, newline stripped.

        Raises:
            SandboxStartError: If no stdout frame arrives within the timeout.
        """
        buffer = ""
        now = asyncio.get_running_loop().time
        deadline = now() + idle_timeout_seconds
        # lint-allow: long-running-loop-kill-switch -- EOF (read_out None) +
        # stdout-only idle deadline + consumer break all terminate the stream.
        while True:
            remaining = deadline - now()
            if remaining <= 0:
                logger.warning(DOCKER_EXECUTE_TIMEOUT, timeout=idle_timeout_seconds)
                msg = f"Streaming container idle past {idle_timeout_seconds}s"
                raise SandboxStartError(msg)
            try:
                message = await asyncio.wait_for(stream.read_out(), timeout=remaining)
            except TimeoutError as exc:
                logger.warning(DOCKER_EXECUTE_TIMEOUT, timeout=idle_timeout_seconds)
                msg = f"Streaming container idle past {idle_timeout_seconds}s"
                raise SandboxStartError(msg) from exc
            if message is None:
                break
            text = self._decode_frame(message.data)
            if message.stream == _STREAM_STDERR:
                self._log_stderr(text)
                continue
            # Real stdout progress: extend the idle deadline.
            deadline = now() + idle_timeout_seconds
            buffer += text
            # Cap the buffer even without a newline: a runaway process emitting
            # a large newline-free stream would otherwise grow it without bound
            # (the idle deadline keeps resetting on each frame), defeating the
            # per-line memory cap that only fires once a newline is found.
            if len(buffer) > _MAX_LINE_CHARS:
                logger.warning(DOCKER_EXEC_STREAM_TRUNCATED, limit=_MAX_LINE_CHARS)
                buffer = buffer[:_MAX_LINE_CHARS]
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if len(line) > _MAX_LINE_CHARS:
                    line = line[:_MAX_LINE_CHARS]
                if line:
                    yield line
        tail = buffer.strip()
        if tail:
            yield tail

    @staticmethod
    def _decode_frame(raw: bytes | bytearray | str) -> str:
        """Decode a stream frame to text, tolerating binary output.

        Returns:
            The decoded frame text.
        """
        if isinstance(raw, bytes | bytearray):
            return raw.decode("utf-8", "replace")
        return str(raw)

    @staticmethod
    def _log_stderr(text: str) -> None:
        """Log container stderr (diagnostics), bounded in size."""
        trimmed = text.strip()
        if trimmed:
            logger.debug(
                DOCKER_EXECUTE_FAILED,
                surface="openhands-container-stderr",
                stderr=trimmed[:_MAX_STDERR_LOG_CHARS],
            )

    @staticmethod
    async def _close_stream(stream: Stream) -> None:
        """Close the attach stream, logging (not swallowing) any close error."""
        try:
            await stream.close()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.debug(
                DOCKER_EXECUTE_FAILED,
                surface="openhands-stream-close",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _log_exit_status(
        self, docker: aiodocker.Docker, container_id: str
    ) -> None:
        """Log the container's exit code when it ended abnormally.

        Called after the stdout stream reaches EOF: an OOM-kill / non-zero
        exit that the container could not report on its own event stream is
        otherwise invisible, so surface the exit code + OOM flag for triage.
        Best-effort: an inspect failure is logged, never raised.
        """
        try:
            info = await docker.containers.container(container_id).show()  # pyright: ignore[reportAttributeAccessIssue]
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING, not DEBUG: this method's whole purpose is surfacing why a
            # container died, so a failed inspect (the one case that leaves the
            # exit invisible) must itself be visible at the default log level.
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                surface="openhands-exit-status",
                container_id=container_id[:12],
                error_type=type(exc).__name__,
            )
            return
        state = info.get("State", {}) if isinstance(info, dict) else {}
        exit_code = state.get("ExitCode")
        oom_killed = bool(state.get("OOMKilled", False))
        if oom_killed or (isinstance(exit_code, int) and exit_code != 0):
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                surface="openhands-container-exit",
                container_id=container_id[:12],
                exit_code=exit_code,
                oom_killed=oom_killed,
            )

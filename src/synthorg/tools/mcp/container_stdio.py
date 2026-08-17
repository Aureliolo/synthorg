# module-kind: adapter
"""An MCP stdio transport whose server process lives in a container.

The SDK's ``stdio_client`` spawns the server as a child of this process, and
this process runs in a hardened image with no shell, no node and no ``npx``.
A containerising wrapper around ``docker run -i`` does not solve that: the
image ships no ``docker`` CLI either, so both spellings of "launch a stdio
MCP server" raise ``FileNotFoundError`` and the whole ``npm_package`` half of
the catalog is unreachable from the deployment that ships it.

The runtime this product speaks to the daemon over is the API, not the CLI.
So this transport creates the container over the API, attaches to its stdin
and stdout before starting it, and hands the session the same stream pair
``stdio_client`` would: line-delimited JSON-RPC in both directions.

Isolation is expressed as ``HostConfig`` rather than flags: every capability
dropped, no new privileges, a read-only root with one writable tmpfs for
``$HOME`` and the npm cache, and the operator's memory / pids / cpu / network
policy. The container keeps the image's own uid, as the agent sandbox does,
because naming a user here would bind the transport to one image's accounts.
Credentials arrive as container environment, so no secret reaches a host
``argv``. Install scripts stay off: a package's ``postinstall`` is the npm
supply-chain vector that version pinning does not close.
"""

import contextlib
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import Final, cast

import aiodocker
import anyio
import anyio.lowlevel
from aiodocker.containers import DockerContainer
from aiodocker.stream import Stream
from aiodocker.types import JSONObject
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.client._transport import TransportStreams
from mcp.shared.message import SessionMessage

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CONTAINER_STDIO_STARTED,
    MCP_CONTAINER_STDIO_STOPPED,
    MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
    MCP_SANDBOX_RESERVED_ENV_DROPPED,
)
from synthorg.tools.mcp.errors import MCPConnectionError
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.tools.sandbox._container_limits import nano_cpus, parse_memory_limit
from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
)

logger = get_logger(__name__)

#: Names the server on its container, so ``docker ps`` on a shared daemon says
#: which MCP server a process belongs to.
_MCP_SERVER_LABEL: Final[str] = "synthorg.mcp.server"

#: aiodocker attach frame id for stderr on a multiplexed (non-TTY) stream.
#: Every other id is stdout, which is the JSON-RPC channel.
_STREAM_STDERR: Final[int] = 2

#: Cap the reassembly buffer so a server emitting a newline-free flood cannot
#: exhaust host memory. Generous because a single MCP response carrying a
#: tool result is one line and can legitimately be large.
_MAX_LINE_CHARS: Final[int] = 4_000_000

#: Cap buffered stderr in the structured log: a server's diagnostics are
#: useful, its binary output is not worth the logging pipeline.
_MAX_STDERR_LOG_CHARS: Final[int] = 400

#: Writable tmpfs the container's ``$HOME`` and npm cache point at, since the
#: root filesystem is read-only.
_CONTAINER_TMP: Final[str] = "/tmp"  # noqa: S108 -- container path, not a host path

#: Enough for one package install; the tmpfs is charged to the container's
#: memory, so it is deliberately smaller than the memory limit.
_TMPFS_SPEC: Final[str] = "rw,nosuid,size=192m"

#: Environment the runtime needs under a read-only root, plus the
#: supply-chain control that keeps a package's install scripts from running.
_RUNTIME_ENV: Final[Mapping[str, str]] = {
    "HOME": _CONTAINER_TMP,
    "NPM_CONFIG_CACHE": f"{_CONTAINER_TMP}/.npm",
    "NPM_CONFIG_IGNORE_SCRIPTS": "true",
}

#: How long to let the server exit on its own before it is killed. It is a
#: stateless request responder, so nothing is lost by not waiting long.
_STOP_TIMEOUT_SECONDS: Final[int] = 5


@asynccontextmanager
async def container_stdio_client(
    *,
    command: str,
    args: list[str],
    env: Mapping[str, str],
    sandbox: MCPSandboxConfig,
    server_name: str,
) -> AsyncGenerator[TransportStreams]:
    """Run an MCP stdio server in a container, yielding its message streams.

    Args:
        command: The server's executable, as the catalog declares it.
        args: Its arguments.
        env: Resolved environment, credentials included.
        sandbox: The container-isolation policy.
        server_name: The server's configured name, for logs.

    Yields:
        The ``(read, write)`` pair a ``ClientSession`` consumes, exactly as
        the SDK's own stdio transport yields.

    Raises:
        MCPConnectionError: The container could not be created or started.
    """
    docker = aiodocker.Docker()
    container: DockerContainer | None = None
    stream: Stream | None = None
    # A failure delivered while the session is open is thrown in at the yield
    # below, and a task group re-raises what escapes its body as an
    # ExceptionGroup. That would cost the caller the type it acts on: the
    # client's reconnect handler retries an MCPConnectionError and nothing
    # else, so a wrapped one reads as permanent. Carried out and re-raised
    # unchanged instead.
    session_failure: BaseException | None = None
    try:
        container = await _create(
            docker,
            command=command,
            args=args,
            env=env,
            sandbox=sandbox,
            server_name=server_name,
        )
        stream = container.attach(stdin=True, stdout=True, stderr=True, logs=False)
        await _start(container, server_name)
        read_writer, read_stream = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](0)
        write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](
            0
        )
        async with anyio.create_task_group() as pumps:
            out_pump = pumps.start_soon(_pump_out, stream, read_writer, server_name)
            in_pump = pumps.start_soon(
                _pump_in, stream, read_writer, write_reader, server_name
            )
            try:
                yield read_stream, write_stream
            except BaseException as exc:  # noqa: BLE001 -- re-raised below
                session_failure = exc
            finally:
                # Closing what the session held is what ends both pumps, so the
                # task group can join instead of waiting on a reader nobody
                # will feed again. Shielded because it must complete even when
                # the caller was cancelled.
                with anyio.CancelScope(shield=True):
                    await _release(read_stream, write_stream, read_writer, write_reader)
                # The backstop for a pump blocked in a daemon read that will
                # never return: cancelled per task rather than by scope, so
                # nothing else running here is taken down with them.
                out_pump.cancel()
                in_pump.cancel()
    finally:
        # Shielded for the same reason, and it matters more here: a container
        # left behind by a cancellation keeps the server running with nothing
        # attached to it for the life of the backend.
        with anyio.CancelScope(shield=True):
            await _teardown(docker, container, stream, server_name)
    if session_failure is not None:
        raise session_failure


async def _create(
    docker: aiodocker.Docker,
    *,
    command: str,
    args: list[str],
    env: Mapping[str, str],
    sandbox: MCPSandboxConfig,
    server_name: str,
) -> DockerContainer:
    """Create the hardened container the server runs in.

    Returns:
        The created (not yet started) container.

    Raises:
        MCPConnectionError: The daemon refused the creation, or the resource
            policy could not be expressed in the units the daemon takes.
    """
    try:
        config = _container_config(command, args, env, sandbox, server_name)
        return await docker.containers.create(config=config)  # pyright: ignore[reportAttributeAccessIssue]
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
            server=server_name,
            phase="create",
            image=sandbox.image,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Server {server_name!r}: could not create its MCP runtime "
            f"container from image {sandbox.image!r}"
        )
        raise MCPConnectionError(msg, context={"server": server_name}) from exc


def _container_config(
    command: str,
    args: list[str],
    env: Mapping[str, str],
    sandbox: MCPSandboxConfig,
    server_name: str,
) -> JSONObject:
    """Express the launch and the isolation policy as a create config.

    Returns:
        The container-creation body for the daemon.

    Raises:
        ValueError: The configured memory limit is not a Docker size string.
    """
    return cast(
        "JSONObject",
        {
            "Image": sandbox.image,
            "Cmd": [command, *args],
            "Env": _env_list(env, server_name),
            "Labels": _labels(sandbox, server_name),
            "WorkingDir": _CONTAINER_TMP,
            # Attached before the start, so no output frame is missed and the
            # session's first request has somewhere to go.
            "OpenStdin": True,
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "StdinOnce": False,
            "Tty": False,
            "HostConfig": {
                "AutoRemove": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {_CONTAINER_TMP: _TMPFS_SPEC},
                "Memory": parse_memory_limit(sandbox.memory_limit),
                "PidsLimit": sandbox.pids_limit,
                "NanoCpus": nano_cpus(float(sandbox.cpus)),
                "NetworkMode": sandbox.network,
            },
        },
    )


def _labels(sandbox: MCPSandboxConfig, server_name: str) -> dict[str, str]:
    """Label the container so the boot reconciliation pass can reclaim it.

    A hard kill of the backend leaves the server running with nothing attached
    to it. ``synthorg.managed`` is what the pass filters on and the deployment
    label is what proves the container is this installation's to remove; a
    container carrying neither is left alone for ever, which is how an
    orphaned runtime would otherwise outlive every reference to it.

    Returns:
        The labels the daemon records on the container.
    """
    labels = {
        MANAGED_LABEL: MANAGED_LABEL_VALUE,
        _MCP_SERVER_LABEL: server_name,
    }
    if sandbox.deployment_id is not None:
        labels[DEPLOYMENT_LABEL] = sandbox.deployment_id
    return labels


def _env_list(env: Mapping[str, str], server_name: str) -> list[str]:
    """Render the container environment, trusted controls last.

    The controls the isolation depends on cannot be supplied by whoever
    configured the server: ``NPM_CONFIG_IGNORE_SCRIPTS=false`` re-enables the
    primary npm RCE vector, and ``HOME`` redirects writes off the one writable
    mount. They win by being merged last, and a collision is reported rather
    than silently overridden, since the operator wrote it expecting it to
    apply.

    Returns:
        The ``KEY=value`` lines the daemon takes.
    """
    for key in env:
        if key in _RUNTIME_ENV:
            logger.warning(
                MCP_SANDBOX_RESERVED_ENV_DROPPED,
                server=server_name,
                key=key,
                note="supplied env key collides with a sandbox control; dropped",
            )
    merged = {**dict(env), **_RUNTIME_ENV}
    return [f"{key}={value}" for key, value in merged.items()]


async def _start(container: DockerContainer, server_name: str) -> None:
    """Start the attached container.

    Raises:
        MCPConnectionError: The daemon refused the start.
    """
    try:
        await container.start()
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
            server=server_name,
            phase="start",
            container_id=_short(container),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Server {server_name!r}: its MCP runtime container would not start"
        raise MCPConnectionError(msg, context={"server": server_name}) from exc
    logger.info(
        MCP_CONTAINER_STDIO_STARTED,
        server=server_name,
        container_id=_short(container),
    )


async def _pump_out(
    stream: Stream,
    writer: MemoryObjectSendStream[SessionMessage | Exception],
    server_name: str,
) -> None:
    """Forward the container's stdout lines to the session as messages.

    A parse failure travels as a value rather than an exception, matching the
    SDK: the session decides what a malformed frame means, and one bad line
    must not tear down a live transport. stderr is logged, never parsed.
    """
    buffer = ""
    async with writer:
        # lint-allow: long-running-loop-kill-switch -- EOF (read_out None), a
        # closed session stream, and the transport's own teardown each end it.
        while True:
            try:
                message = await stream.read_out()
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                _log_transport_error(exc, server_name, phase="read")
                return
            if message is None:
                return
            text = _decode(message.data)
            if message.stream == _STREAM_STDERR:
                _log_stderr(text, server_name)
                continue
            buffer += text
            if len(buffer) > _MAX_LINE_CHARS:
                buffer = buffer[:_MAX_LINE_CHARS]
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    await writer.send(_parse(line))
                except anyio.ClosedResourceError, anyio.BrokenResourceError:
                    return


async def _pump_in(
    stream: Stream,
    read_writer: MemoryObjectSendStream[SessionMessage | Exception],
    reader: MemoryObjectReceiveStream[SessionMessage],
    server_name: str,
) -> None:
    """Forward the session's messages to the container's stdin.

    A stdin that will not accept writes closes the read side too, so a
    pending request sees the connection end rather than hanging for ever.
    Teardown closes this stream under the iteration, which is the ordinary
    way a session ends rather than a failure to report.
    """
    async with reader:
        try:
            async for session_message in reader:
                payload = session_message.message.model_dump_json(
                    by_alias=True, exclude_unset=True
                )
                try:
                    await stream.write_in(f"{payload}\n".encode())
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    _log_transport_error(exc, server_name, phase="write")
                    with contextlib.suppress(
                        anyio.ClosedResourceError, anyio.BrokenResourceError
                    ):
                        await read_writer.aclose()
                    return
        except anyio.ClosedResourceError, anyio.BrokenResourceError:
            return


def _parse(line: str) -> SessionMessage | Exception:
    """Parse one stdout line into a session message.

    Returns:
        The message, or the parse error as a value for the session to
        surface, which is what the SDK's own transport does.
    """
    try:
        return SessionMessage(
            types.jsonrpc_message_adapter.validate_json(line, by_name=False)
        )
    except ValueError as exc:
        return exc


def _decode(raw: bytes | bytearray | str) -> str:
    """Decode one attach frame, tolerating binary output.

    Returns:
        The frame as text, undecodable bytes replaced.
    """
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _short(container: DockerContainer) -> str:
    """Render a container id at the length logs elsewhere use.

    Returns:
        The first twelve characters of the id.
    """
    return str(container.id)[:12]


def _log_stderr(text: str, server_name: str) -> None:
    """Record a server's stderr chatter without letting it flood the log."""
    trimmed = text.strip()
    if trimmed:
        logger.debug(
            MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
            server=server_name,
            phase="stderr",
            output=trimmed[:_MAX_STDERR_LOG_CHARS],
        )


def _log_transport_error(exc: Exception, server_name: str, *, phase: str) -> None:
    """Record a mid-session transport failure."""
    logger.warning(
        MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
        server=server_name,
        phase=phase,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


async def _release(
    *streams: MemoryObjectReceiveStream[SessionMessage | Exception]
    | MemoryObjectSendStream[SessionMessage]
    | MemoryObjectSendStream[SessionMessage | Exception]
    | MemoryObjectReceiveStream[SessionMessage],
) -> None:
    """Close every message stream, whatever any one of them does."""
    for stream in streams:
        with contextlib.suppress(Exception):
            await stream.aclose()
    # One pass so a pump unblocked by the closes leaves through its own
    # except path before the caller cancels the group under it.
    await anyio.lowlevel.checkpoint()


async def _teardown(
    docker: aiodocker.Docker,
    container: DockerContainer | None,
    stream: Stream | None,
    server_name: str,
) -> None:
    """Close the attach stream and destroy the container, whatever happened.

    Each step is guarded on its own: a failure closing the stream must still
    reach the delete, or the container outlives every reference to it.
    """
    if stream is not None:
        with contextlib.suppress(Exception):
            await stream.close()
    if container is not None:
        container_id = _short(container)
        with contextlib.suppress(Exception):
            await container.stop(timeout=_STOP_TIMEOUT_SECONDS)
        with contextlib.suppress(Exception):
            await container.delete(force=True)
        logger.info(
            MCP_CONTAINER_STDIO_STOPPED,
            server=server_name,
            container_id=container_id,
        )
    with contextlib.suppress(Exception):
        await docker.close()


__all__ = ["container_stdio_client"]

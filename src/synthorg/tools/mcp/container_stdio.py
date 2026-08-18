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
from collections.abc import AsyncGenerator, Awaitable, Mapping
from contextlib import asynccontextmanager
from typing import Final

import aiodocker
import anyio
import anyio.lowlevel
from aiodocker.containers import DockerContainer
from aiodocker.stream import Stream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types

# A private module, deliberately: ``TransportStreams`` is the type the SDK's
# own transports yield and this one has to match them, and in the pinned
# release it is exported from nowhere public. The exact ``mcp==2.0.0`` pin in
# ``pyproject.toml`` is what makes that safe, so an SDK upgrade must recheck
# this import rather than assume it survived.
from mcp.client._transport import TransportStreams
from mcp.shared.message import SessionMessage

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CONTAINER_STDIO_LINE_DISCARDED,
    MCP_CONTAINER_STDIO_STARTED,
    MCP_CONTAINER_STDIO_STOPPED,
    MCP_CONTAINER_STDIO_TEARDOWN_FAILED,
    MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
)
from synthorg.tools.mcp.container_spec import container_config
from synthorg.tools.mcp.errors import MCPConnectionError
from synthorg.tools.mcp.sandbox import MCPSandboxConfig

logger = get_logger(__name__)

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

#: How long to let the server exit on its own before it is killed. It is a
#: stateless request responder, so nothing is lost by not waiting long.
_STOP_TIMEOUT_SECONDS: Final[int] = 5

#: How long to wait for the daemon to finish removing the container. The
#: client's own default here is NO total timeout, and the removal runs inside
#: a shielded scope, so an unbounded wait on a daemon stuck mid-removal has
#: no cancellation path and hangs the transport's exit for ever. Longer than
#: the stop window because a force-removal does real filesystem work.
_DELETE_TIMEOUT_SECONDS: Final[int] = 30

#: The two steps that take no timeout of their own: closing the attach stream
#: and closing the daemon client. Both are local socket work, so a wait past
#: this is a wedged socket rather than slow progress, and an unbounded one
#: sits inside the same shielded scope with no cancellation path.
_CLOSE_TIMEOUT_SECONDS: Final[int] = 5

#: What a full teardown can cost when every step runs to its own ceiling.
#: Published because the caller that wraps this transport's exit in a timeout
#: has to be at least this patient: a shorter one abandons a teardown that was
#: merely slow, and the client latches itself permanently unrestartable over a
#: container that was in fact being removed.
TEARDOWN_BUDGET_SECONDS: Final[int] = (
    _CLOSE_TIMEOUT_SECONDS * 2 + _STOP_TIMEOUT_SECONDS + _DELETE_TIMEOUT_SECONDS
)


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
        stream = await _attached_and_started(container, server_name)
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


async def _attached_and_started(container: DockerContainer, server_name: str) -> Stream:
    """Attach to *container*'s stdio, then start it, in that order.

    ``attach`` builds the Stream and performs NO I/O: the client opens the
    connection lazily, inside the first ``read_out`` / ``write_in``. Left
    alone, that first call happens in a pump, i.e. AFTER the start, and a
    server that writes on startup or dies immediately has its output dropped
    by the daemon with nobody attached. ``logs=False`` means there is no
    replay to recover it from.

    Entering the stream here performs the attach before the start, which is
    what the rest of this module says happens, and it also settles which task
    runs the lazy init: both pumps would otherwise race it, and its guard is
    unlocked, so each could open its own connection and the loser's would be
    leaked with the winner overwriting the shared queue.

    Either returns an entered stream with the container running, or leaves
    nothing open. The caller holds the stream in a variable its ``finally``
    reads, and an assignment only happens once this returns, so a stream
    entered here and abandoned on the way out would be invisible to that
    teardown: the attach is a live upgraded socket the daemon keeps alive,
    and the library closes it on nothing but an explicit call.

    Returns:
        The entered stream, with the container running.

    Raises:
        MCPConnectionError: The stream could not be attached, or the
            container could not be started.
    """
    stream = container.attach(stdin=True, stdout=True, stderr=True, logs=False)
    try:
        await stream.__aenter__()
    except BaseException as exc:
        # BaseException, not Exception: entering opens the connection partway
        # through, so a cancellation delivered after the socket exists but
        # before the enter returns leaves one open that the caller was never
        # handed and cannot close.
        await _close_unentered(stream, server_name)
        reraise_critical(exc)
        if isinstance(exc, Exception):
            logger.warning(
                MCP_CONTAINER_STDIO_TRANSPORT_ERROR,
                server=server_name,
                phase="attach",
                container_id=_short(container),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # Classified like its siblings rather than left raw: the client
            # retries an MCPConnectionError and treats everything else as
            # permanent, so an attach that lost a race with the daemon has to
            # arrive wearing the type that gets another go.
            msg = f"Server {server_name!r}: its MCP runtime container would not attach"
            raise MCPConnectionError(msg, context={"server": server_name}) from exc
        # A cancellation is not a transport failure and must reach the caller
        # as itself, or the scope that asked for it never unwinds.
        raise
    try:
        await _start(container, server_name)
    except BaseException:
        # Cancellation included: the socket is open either way, and the
        # caller cannot close what it was never handed.
        await _close_unentered(stream, server_name)
        raise
    return stream


async def _close_unentered(stream: Stream, server_name: str) -> None:
    """Release a stream the caller never received.

    Shielded, because both callers run while the failure that brought them
    here may itself be a cancellation: an unshielded await inside such a
    handler is cancelled at once, so the close never runs and leaves exactly
    the socket the handler exists to release. Nothing downstream can clean it
    up either, since the value was never returned.
    """
    with anyio.CancelScope(shield=True):
        await _guarded(
            stream.close(),
            server_name,
            step="stream_close",
            limit_seconds=_CLOSE_TIMEOUT_SECONDS,
        )


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
        config = container_config(command, args, env, sandbox, server_name)
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
    discarding = False
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
            if discarding:
                # Still inside the oversized line: drop everything up to and
                # including the newline that ends it, then carry on normally.
                _, separator, tail = buffer.partition("\n")
                buffer = tail if separator else ""
                if not separator:
                    continue
                discarding = False
            elif len(buffer) > _MAX_LINE_CHARS and "\n" not in buffer:
                # Truncating to the HEAD would keep a prefix that can never
                # complete a line, and every later chunk would be appended
                # and trimmed straight back off it: the transport would never
                # see a newline again for the life of the session, silently,
                # however well behaved the server then became. Discard the
                # oversized line and resynchronise on the next newline, which
                # costs one unparseable message instead of the connection.
                _log_oversized_line(len(buffer), server_name)
                buffer = ""
                discarding = True
                continue
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


def _log_oversized_line(dropped_chars: int, server_name: str) -> None:
    """Record a line discarded for exceeding the reassembly cap.

    WARNING because the caller loses a message it will never be told about
    otherwise: the session simply never sees that response, and a request
    waiting on it waits for its own timeout instead.
    """
    logger.warning(
        MCP_CONTAINER_STDIO_LINE_DISCARDED,
        server=server_name,
        dropped_chars=dropped_chars,
        max_line_chars=_MAX_LINE_CHARS,
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


async def _guarded(
    step_call: Awaitable[object],
    server_name: str,
    *,
    step: str,
    container_id: str | None = None,
    limit_seconds: float | None = None,
) -> bool:
    """Await one teardown step, reporting rather than swallowing a failure.

    Args:
        step_call: The teardown coroutine to await.
        server_name: Which server's teardown this is.
        step: Which step, so the log says what did not happen.
        container_id: The container, when the step has one.
        limit_seconds: Ceiling for a step that takes none of its own. A step
            that hangs here would hang the whole shielded exit, and expiring
            is reported through the same path as any other failure so the
            container is still recorded as left behind.

    Returns:
        Whether the step succeeded.
    """
    try:
        if limit_seconds is None:
            await step_call
        else:
            with anyio.fail_after(limit_seconds):
                await step_call
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- one failed teardown step must still reach
        # the next, or a container outlives every reference to it. Reported,
        # never silent: this is the only record that it was left behind.
        reraise_critical(exc)
        logger.warning(
            MCP_CONTAINER_STDIO_TEARDOWN_FAILED,
            server=server_name,
            step=step,
            container_id=container_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    return True


async def _teardown(
    docker: aiodocker.Docker,
    container: DockerContainer | None,
    stream: Stream | None,
    server_name: str,
) -> None:
    """Close the attach stream and destroy the container, whatever happened.

    Each step is guarded on its own: a failure closing the stream must still
    reach the delete, or the container outlives every reference to it. Every
    guard reports, because this runs shielded inside a ``finally`` where a
    silent failure leaves a credentialed third-party server running with
    nothing attached to it and nobody told. The removal is what the success
    line speaks for, so it is the one whose outcome decides what is logged:
    saying "stopped" after a delete that raised describes a container that
    is still there.
    """
    if stream is not None:
        await _guarded(
            stream.close(),
            server_name,
            step="stream_close",
            limit_seconds=_CLOSE_TIMEOUT_SECONDS,
        )
    if container is not None:
        container_id = _short(container)
        # ``t`` is the grace period the daemon gives the process before the
        # kill; ``timeout`` is how long we wait for the daemon to answer.
        # Passing the constant as ``timeout`` alone left the documented
        # window unset and the daemon's own default in force.
        await _guarded(
            container.stop(t=_STOP_TIMEOUT_SECONDS, timeout=_STOP_TIMEOUT_SECONDS),
            server_name,
            step="container_stop",
            container_id=container_id,
        )
        # Bounded deliberately: the default resolves to no total timeout at
        # all, and this await sits inside a shielded scope, so a daemon that
        # hangs mid-removal (a stuck unmount, a cgroup that will not clear)
        # would wedge the context manager's exit for ever with no
        # cancellation path left to anyone.
        removed = await _guarded(
            container.delete(force=True, timeout=_DELETE_TIMEOUT_SECONDS),
            server_name,
            step="container_delete",
            container_id=container_id,
        )
        if removed:
            logger.info(
                MCP_CONTAINER_STDIO_STOPPED,
                server=server_name,
                container_id=container_id,
            )
    await _guarded(
        docker.close(),
        server_name,
        step="client_close",
        limit_seconds=_CLOSE_TIMEOUT_SECONDS,
    )


__all__ = ["container_stdio_client"]

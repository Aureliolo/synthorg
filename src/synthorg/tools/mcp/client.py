# module-kind: adapter
"""MCP client -- thin async wrapper over the MCP SDK.

Manages a single connection to an MCP server and provides
tool discovery and invocation through the MCP protocol.
"""

import asyncio
import copy
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Final, NoReturn, Protocol, Self, runtime_checkable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.env_var_safety import validate_credential_env_var_name
from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CLIENT_CONNECTED,
    MCP_CLIENT_CONNECTING,
    MCP_CLIENT_CONNECTION_FAILED,
    MCP_CLIENT_DISCONNECT_FAILED,
    MCP_CLIENT_DISCONNECTED,
    MCP_CLIENT_RECONNECT_RETRY,
    MCP_CLIENT_RECONNECTING,
    MCP_CREDENTIAL_SOURCE_MISSING,
    MCP_CREDENTIALS_INJECTED,
    MCP_DISCOVERY_COMPLETE,
    MCP_DISCOVERY_FAILED,
    MCP_DISCOVERY_FILTERED,
    MCP_DISCOVERY_START,
    MCP_INVOKE_FAILED,
    MCP_INVOKE_START,
    MCP_INVOKE_SUCCESS,
    MCP_INVOKE_TIMEOUT,
    MCP_SANDBOX_WRAPPED,
)
from synthorg.observability.metrics_hub import record_client_disconnect
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.errors import (
    MCPConnectionError,
    MCPDiscoveryError,
    MCPInvocationError,
    MCPTimeoutError,
)
from synthorg.tools.mcp.models import MCPRawResult, MCPToolInfo
from synthorg.tools.mcp.sandbox import MCPSandboxConfig, wrap_stdio_in_sandbox

logger = get_logger(__name__)

# Upper bound on the graceful transport close. A hung stdio child (or a
# detached ``npx``-spawned grandchild) must not stall a hot-reload/shutdown
# that calls disconnect() synchronously, so the close is time-boxed. It must
# stay comfortably above the MCP SDK's own SIGTERM->SIGKILL teardown budget
# (~4s) so this outer bound never cancels the SDK mid-escalation and orphans
# the child; per-server ``connect_timeout_seconds`` is separately capped low.
_DISCONNECT_TIMEOUT_SECONDS: Final[float] = 10.0

# Bounded self-heal backoff for reconnect: a transient blip retries with
# short exponential backoff, held to a few attempts so the session lock is
# never held for an unbounded stall.
_RECONNECT_MAX_ATTEMPTS: Final[int] = 3
_RECONNECT_BACKOFF_BASE_SECONDS: Final[float] = 0.2
_RECONNECT_BACKOFF_CAP_SECONDS: Final[float] = 2.0


@runtime_checkable
class MCPCredentialResolver(Protocol):
    """Resolves a bound connection's decrypted credentials by name."""

    async def get_credentials(self, name: str) -> dict[str, str]:
        """Return the decrypted credential fields for connection ``name``."""
        ...


class MCPClient:
    """Async client for a single MCP server.

    Wraps the MCP SDK's ``ClientSession`` to provide connection
    management, tool discovery, and tool invocation. A lock
    serializes all session access to prevent interleaving.

    Args:
        config: Server connection configuration.
        credential_source: Resolver for the bound connection's secrets,
            injected into the spawned stdio process at connect time. ``None``
            leaves a connection-bound server without credentials (it will
            likely fail to authenticate, logged loudly at connect).
        sandbox: Container-isolation policy for stdio servers. When enabled,
            the server runs inside ``docker run -i`` under cap-drop /
            no-new-privileges / read-only rootfs / resource limits. ``None``
            (or disabled) spawns on the host.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        credential_source: MCPCredentialResolver | None = None,
        sandbox: MCPSandboxConfig | None = None,
    ) -> None:
        self._config = config
        self._credential_source = credential_source
        self._sandbox = sandbox
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        # Bounded backoff so a transient reconnect blip self-heals without
        # an unbounded stall while the session lock is held.
        self._reconnect_retry = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, MCPConnectionError),
            max_attempts=_RECONNECT_MAX_ATTEMPTS,
            base=_RECONNECT_BACKOFF_BASE_SECONDS,
            cap=_RECONNECT_BACKOFF_CAP_SECONDS,
            event=MCP_CLIENT_RECONNECT_RETRY,
        )

    @property
    def config(self) -> MCPServerConfig:
        """Server connection configuration (read-only)."""
        return self._config

    @property
    def is_connected(self) -> bool:
        """Whether the client has an active session."""
        return self._session is not None

    @property
    def server_name(self) -> str:
        """Name of the configured server."""
        return self._config.name

    async def connect(self) -> None:
        """Establish a connection to the MCP server.

        Uses ``AsyncExitStack`` for guaranteed cleanup on any
        exception (including ``CancelledError``); ``stack.pop_all()``
        transfers ownership to ``self._exit_stack`` on the success
        path only, so ``disconnect()`` controls the eventual close.

        Raises:
            MCPConnectionError: If the connection fails.
            RuntimeError: If already connected.
        """
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        """Connect assuming the session lock is already held.

        Split out so :meth:`reconnect` can run disconnect+connect inside a
        single lock acquisition (atomic reconnect), rather than releasing and
        re-acquiring between the two and racing a concurrent healer.

        Raises:
            MCPConnectionError: If the connection fails.
            RuntimeError: If already connected.
        """
        if self._session is not None:
            msg = f"Already connected to {self._config.name!r}"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise RuntimeError(msg)
        logger.info(
            MCP_CLIENT_CONNECTING,
            server=self._config.name,
            transport=self._config.transport,
        )
        async with AsyncExitStack() as stack:
            session = await self._establish_session(stack)
            # ``_exit_stack`` first so a CancelledError between
            # these two assignments cannot leave a zombie state
            # (``is_connected`` true, transport closed): once
            # ``pop_all()`` lands on ``self``, ``disconnect()``
            # owns cleanup regardless of which line is interrupted.
            self._exit_stack = stack.pop_all()
            self._session = session
        logger.info(
            MCP_CLIENT_CONNECTED,
            server=self._config.name,
        )

    async def _establish_session(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Run the timeout-bounded connect and translate exceptions.

        Failures are logged at WARNING (not EXCEPTION) so ``exc_info``
        cannot re-bind ``str(exc)`` and bypass the
        ``safe_error_description`` scrub on credential-bearing paths.

        Returns:
            Result of type ``ClientSession``.

        Raises:
            MCPConnectionError: If the related operation fails.
        """
        try:
            return await asyncio.wait_for(
                self._connect_with_stack(stack),
                timeout=self._config.connect_timeout_seconds,
            )
        except TimeoutError as exc:
            msg = (
                f"Connection to {self._config.name!r} timed out "
                f"after {self._config.connect_timeout_seconds}s"
            )
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            self._raise_connection_error(msg, exc)
        except MCPConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # ``safe_error_description`` also scrubs the propagated
            # message: raw exc args can leak OAuth tokens / Bearer
            # headers / connection strings to upstream callers.
            msg = (
                f"Failed to connect to {self._config.name!r}: "
                f"{safe_error_description(exc)}"
            )
            self._raise_connection_error(msg, exc)

    def _raise_connection_error(
        self,
        message: str,
        exc: BaseException,
    ) -> NoReturn:
        """Raise ``MCPConnectionError`` with the server context attached.

        Raises:
            MCPConnectionError: If the related operation fails.
        """
        raise MCPConnectionError(
            message,
            context={
                "server": self._config.name,
                "transport": self._config.transport,
            },
        ) from exc

    async def _connect_with_stack(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Connect via the appropriate transport and initialize.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected and initialized ``ClientSession``.
        """
        if self._config.transport == "stdio":
            session = await self._connect_stdio(stack)
        else:
            session = await self._connect_http(stack)
        await session.initialize()
        return session

    async def disconnect(self) -> None:
        """Close the connection and release resources."""
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        """Close the transport assuming the session lock is already held."""
        # Map MCP config transport ('stdio' / 'streamable_http') to the
        # bounded ``synthorg_client_disconnects_total`` transport label
        # so each MCP transport keeps its own time-series rather than
        # all collapsing into ``mcp_stdio``.
        metric_transport = (
            "mcp_stdio" if self._config.transport == "stdio" else "mcp_http"
        )
        if self._exit_stack is not None:
            try:
                await asyncio.wait_for(
                    self._exit_stack.aclose(),
                    timeout=_DISCONNECT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    MCP_CLIENT_DISCONNECT_FAILED,
                    server=self._config.name,
                    error=f"disconnect timed out after "
                    f"{_DISCONNECT_TIMEOUT_SECONDS}s; child may be hung",
                )
                record_client_disconnect(
                    transport=metric_transport,
                    reason="timeout",
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    MCP_CLIENT_DISCONNECT_FAILED,
                    server=self._config.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                record_client_disconnect(
                    transport=metric_transport,
                    reason="transport_error",
                )
            else:
                logger.info(
                    MCP_CLIENT_DISCONNECTED,
                    server=self._config.name,
                )
                record_client_disconnect(
                    transport=metric_transport,
                    reason="client_initiated",
                )
            finally:
                self._session = None
                self._exit_stack = None

    async def list_tools(self) -> tuple[MCPToolInfo, ...]:
        """Discover tools from the connected server.

        Applies ``enabled_tools`` / ``disabled_tools`` filters
        from the server configuration.

        Returns:
            Filtered tuple of discovered tool metadata.

        Raises:
            MCPDiscoveryError: If discovery fails.
        """
        async with self._lock:
            session = self._require_session()
            logger.info(
                MCP_DISCOVERY_START,
                server=self._config.name,
            )
            try:
                result = await session.list_tools()
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    MCP_DISCOVERY_FAILED,
                    server=self._config.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = (
                    f"Tool discovery failed for {self._config.name!r}: "
                    f"{safe_error_description(exc)}"
                )
                raise MCPDiscoveryError(
                    msg,
                    context={"server": self._config.name},
                ) from exc

        tools = tuple(
            MCPToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=(copy.deepcopy(t.inputSchema) if t.inputSchema else {}),
                server_name=self._config.name,
            )
            for t in result.tools
        )

        filtered = self._apply_filters(tools)
        logger.info(
            MCP_DISCOVERY_COMPLETE,
            server=self._config.name,
            total=len(tools),
            after_filter=len(filtered),
        )
        return filtered

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> MCPRawResult:
        """Invoke a tool on the connected server.

        Acquires the session lock to respect MCP's sequential
        protocol constraint. Applies the configured timeout.

        Args:
            tool_name: Name of the tool to invoke.
            arguments: Arguments to pass to the tool.

        Returns:
            Raw result from the MCP server.

        Raises:
            MCPTimeoutError: If the invocation times out.
            MCPInvocationError: If the invocation fails.
        """
        logger.debug(
            MCP_INVOKE_START,
            server=self._config.name,
            tool=tool_name,
        )
        invocation_error: Exception | None = None
        async with self._lock:
            session = self._require_session()
            try:
                result = await asyncio.wait_for(
                    session.call_tool(tool_name, arguments),
                    timeout=self._config.timeout_seconds,
                )
            except TimeoutError as exc:
                logger.warning(
                    MCP_INVOKE_TIMEOUT,
                    server=self._config.name,
                    tool=tool_name,
                    timeout=self._config.timeout_seconds,
                )
                msg = f"Tool {tool_name!r} timed out on {self._config.name!r}"
                raise MCPTimeoutError(
                    msg,
                    context={
                        "server": self._config.name,
                        "tool": tool_name,
                        "timeout": self._config.timeout_seconds,
                    },
                ) from exc
            except Exception as exc:  # noqa: BLE001 -- re-raised as typed error below
                reraise_critical(exc)
                logger.warning(
                    MCP_INVOKE_FAILED,
                    server=self._config.name,
                    tool=tool_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                invocation_error = exc
            else:
                logger.info(
                    MCP_INVOKE_SUCCESS,
                    server=self._config.name,
                    tool=tool_name,
                )
                return MCPRawResult(
                    content=tuple(result.content),
                    is_error=result.isError or False,
                    structured_content=(
                        copy.deepcopy(result.structuredContent)
                        if result.structuredContent is not None
                        else None
                    ),
                )

        # A raised (non-timeout) call means the session/transport is likely
        # dead (MCP tool-level errors come back as ``is_error``, not
        # exceptions). Heal the connection for subsequent calls, but surface
        # THIS call's failure rather than auto-retrying: an MCP tool may be a
        # mutation, so a silent retry could double-execute a side effect.
        await self._heal_after_failure()
        msg = (
            f"Tool {tool_name!r} failed on {self._config.name!r}: "
            f"{safe_error_description(invocation_error)}"
        )
        raise MCPInvocationError(
            msg,
            context={"server": self._config.name, "tool": tool_name},
        ) from invocation_error

    async def _heal_after_failure(self) -> None:
        """Best-effort reconnect after a transport failure, for the next call.

        Swallows reconnect errors (the current call already failed and is about
        to raise); a still-broken server surfaces on the next invocation.
        """
        try:
            await self.reconnect()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                MCP_CLIENT_RECONNECTING,
                server=self._config.name,
                note="heal after tool failure did not reconnect",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def reconnect(self) -> None:
        """Disconnect and reconnect to the server, atomically.

        Runs disconnect+connect under a single lock acquisition so two
        concurrent healers cannot interleave a disconnect against the other's
        fresh connect (which would either tear down a healthy session or raise
        a spurious "already connected"). A transient connect failure retries
        with bounded backoff.

        Raises:
            MCPConnectionError: If the reconnection fails after retries.
        """
        logger.info(
            MCP_CLIENT_RECONNECTING,
            server=self._config.name,
        )
        async with self._lock:
            await self._reconnect_retry.execute(
                self._reconnect_once,
                server=self._config.name,
            )

    async def _reconnect_once(self) -> None:
        """One atomic disconnect+connect cycle (lock already held)."""
        await self._disconnect_locked()
        await self._connect_locked()

    async def __aenter__(self) -> Self:
        """Enter async context: connect to server.

        Returns:
            Result of type ``Self``.
        """
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Exit async context: disconnect from server."""
        await self.disconnect()

    # ── Private helpers ──────────────────────────────────────────

    def _require_session(self) -> ClientSession:
        """Return the active session or raise.

        Returns:
            The active ``ClientSession``.

        Raises:
            MCPConnectionError: If not connected.
        """
        if self._session is None:
            msg = f"Not connected to {self._config.name!r}"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        return self._session

    async def _connect_stdio(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Set up a stdio transport connection.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected ``ClientSession`` (not yet initialized).

        Raises:
            MCPConnectionError: If the related operation fails.
        """
        if self._config.command is None:
            msg = f"Server {self._config.name!r}: stdio transport requires 'command'"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        args, env = await self._resolve_stdio_launch()
        command = self._config.command
        if self._sandbox is not None and self._sandbox.enabled:
            command, args, sandbox_env = wrap_stdio_in_sandbox(
                command=command,
                args=args,
                env=env or {},
                sandbox=self._sandbox,
            )
            # Pass a dict (even empty) so the SDK merges it over
            # get_default_environment(): the docker process keeps PATH and gains
            # the forwarded secrets that ``--env KEY`` references by name.
            env = sandbox_env
            # A security-relevant launch rewrite: trace it (no secrets) so an
            # operator can confirm a stdio server was containerised.
            logger.debug(
                MCP_SANDBOX_WRAPPED,
                server=self._config.name,
                image=self._sandbox.image,
                memory_limit=self._sandbox.memory_limit,
                network=self._sandbox.network,
            )
        params = StdioServerParameters(
            command=command,
            args=args,
            env=env,
        )
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(params),
        )
        return await stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )

    async def _resolve_stdio_launch(self) -> tuple[list[str], dict[str, str] | None]:
        """Resolve the launch args + env, injecting bound-connection secrets.

        The connection's decrypted credentials are mapped into the environment
        variables (``credential_env_map``) the target server expects, at connect
        time so secrets are never persisted in the stored config. Secrets are
        forwarded by environment variable only: a value never lands in the
        process argv (visible via ``ps`` / ``/proc``). Every enabled-but-
        unresolvable state (bound connection with no map, no resolver, or a
        short injection) is logged loudly rather than silently pretending the
        server is authenticated.

        Returns:
            The final ``(args, env)`` pair for ``StdioServerParameters``.
        """
        args = list(self._config.args)
        env = dict(self._config.env)
        name = self._config.connection_name
        if name is None:
            return args, (env or None)
        env_map = self._config.credential_env_map
        if not env_map:
            logger.warning(
                MCP_CREDENTIAL_SOURCE_MISSING,
                server=self._config.name,
                connection=name,
                note="connection bound but entry declares no credential fields",
            )
            return args, (env or None)
        if self._credential_source is None:
            logger.warning(
                MCP_CREDENTIAL_SOURCE_MISSING,
                server=self._config.name,
                connection=name,
                note="connection bound but no credential source; unauthenticated",
            )
            return args, (env or None)
        creds = await self._credential_source.get_credentials(name)
        injected = 0
        for field, env_var in env_map.items():
            self._screen_injection_target(env_var)
            value = creds.get(field)
            if value:
                env[env_var] = value
                injected += 1
        self._log_injection(name, injected, len(env_map))
        return args, (env or None)

    def _screen_injection_target(self, env_var: str) -> None:
        """Re-screen a credential target env-var name at the spawn boundary.

        ``credential_env_map`` is screened at config construction, but the
        model's ``frozen=True`` only blocks field reassignment: the nested dict
        stays mutable in place, so a post-validation edit could redirect a
        secret at a loader/process-control variable (``LD_PRELOAD`` /
        ``NODE_OPTIONS`` / ``PATH``). Revalidate here, immediately before the
        value is injected into the child environment, and fail closed rather
        than spawn with a hijacked target.

        Raises:
            MCPConnectionError: If the target env-var name is unsafe.
        """
        try:
            validate_credential_env_var_name(env_var)
        except ValueError as exc:
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                reason="credential injection target rejected at spawn",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Server {self._config.name!r}: credential injection target "
                f"env-var name is unsafe"
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            ) from exc

    def _log_injection(self, connection: str, injected: int, expected: int) -> None:
        """Log credential injection, escalating a short injection to WARNING.

        ``injected < expected`` means the resolved connection is missing a
        field the entry declares (a schema mismatch between the catalog entry
        and the connection's credential type); it presents downstream as an
        opaque upstream auth failure, so it is surfaced here rather than logged
        identically to a full injection.
        """
        if injected < expected:
            logger.warning(
                MCP_CREDENTIALS_INJECTED,
                server=self._config.name,
                connection=connection,
                injected_fields=injected,
                expected_fields=expected,
                note="fewer credential fields resolved than the entry declares",
            )
            return
        logger.info(
            MCP_CREDENTIALS_INJECTED,
            server=self._config.name,
            connection=connection,
            injected_fields=injected,
        )

    async def _connect_http(
        self,
        stack: AsyncExitStack,
    ) -> ClientSession:
        """Set up a streamable HTTP transport connection.

        Args:
            stack: Exit stack for resource management.

        Returns:
            Connected ``ClientSession`` (not yet initialized).

        Raises:
            MCPConnectionError: If the related operation fails.
        """
        if self._config.url is None:
            msg = f"Server {self._config.name!r}: streamable_http requires 'url'"
            logger.warning(
                MCP_CLIENT_CONNECTION_FAILED,
                server=self._config.name,
                error=msg,
            )
            raise MCPConnectionError(
                msg,
                context={"server": self._config.name},
            )
        http_client = await stack.enter_async_context(
            create_mcp_http_client(
                headers=dict(self._config.headers) if self._config.headers else None,
            ),
        )
        read_stream, write_stream, _ = await stack.enter_async_context(
            streamable_http_client(
                url=self._config.url,
                http_client=http_client,
            ),
        )
        return await stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )

    def _apply_filters(
        self,
        tools: tuple[MCPToolInfo, ...],
    ) -> tuple[MCPToolInfo, ...]:
        """Apply enabled/disabled tool filters.

        Args:
            tools: All discovered tools.

        Returns:
            Filtered tool tuple.
        """
        result = tools

        if self._config.enabled_tools is not None:
            allowed = set(self._config.enabled_tools)
            before = len(result)
            result = tuple(t for t in result if t.name in allowed)
            if len(result) < before:
                logger.debug(
                    MCP_DISCOVERY_FILTERED,
                    server=self._config.name,
                    filter_type="enabled_tools",
                    before=before,
                    after=len(result),
                )

        if self._config.disabled_tools:
            blocked = set(self._config.disabled_tools)
            before = len(result)
            result = tuple(t for t in result if t.name not in blocked)
            if len(result) < before:
                logger.debug(
                    MCP_DISCOVERY_FILTERED,
                    server=self._config.name,
                    filter_type="disabled_tools",
                    before=before,
                    after=len(result),
                )

        return result

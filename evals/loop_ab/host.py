# module-kind: orchestrator
"""The recording host: the A/B recorder serving its own LLM gateway.

The OpenHands loop authenticates to the gateway with a per-run bearer minted by
the *same* :class:`~synthorg.llm.gateway_token.GatewaySigner` instance the
gateway verifies with, and that instance is built per process and never
persisted. So the recorder stops trying to borrow one and owns it: it boots the
real application against a scratch database, serves it on a local port, and
reads the signer off the state the boot wiring populated. Mint and verify are
the same instance because they are the same process.

Two properties fall out of hosting rather than borrowing. The gateway's cost
ledger belongs to the recorder, so the OpenHands leg's spend (which is recorded
inside the container's calls, not the engine's) is finally visible to the
scoreboard. And the credentialed-MCP surface the SDK insists on is the real
one, served under the shipped empty capability grant, so the harness completes
the handshake while reaching no credentialed tool at all.

Nothing here is persisted beyond a scratch database removed on exit; the signer
never leaves memory.
"""

import asyncio
import base64
import os
import secrets
import shutil
import socket
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Final, Self

import uvicorn
from litestar import Litestar

from evals.errors import LoopAbGatewayUnavailableError
from synthorg.api.app import create_app
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.state import AppState
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_LOOP_AB_HOST_STARTED,
    EVALS_LOOP_AB_HOST_STOPPED,
)
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.state import settings_service_of

logger = get_logger(__name__)

#: Mounted route prefixes, matching what the two controllers declare.
_GATEWAY_PATH: Final[str] = "/api/v1/gateway/v1"
_MCP_PATH: Final[str] = "/api/v1/mcp-gateway"

#: Where the OpenHands container reaches the recorder from. The container joins
#: the sidecar's network namespace, so its loopback is the sidecar's own; the
#: wiring gives the sidecar this alias and nothing else resolves.
DEFAULT_CONTAINER_HOST: Final[str] = "host.docker.internal"

#: The Docker bridge cannot reach a loopback-only listener, so a recording that
#: drives containers has to bind every interface. Narrow it with ``--bind-host``
#: where the bridge address is known and stable.
DEFAULT_BIND_HOST: Final[str] = "0.0.0.0"  # noqa: S104

#: Grace period for the serving task to unwind after it is asked to exit.
_STOP_TIMEOUT_SECONDS: Final[float] = 30.0

_SCRATCH_DB_NAME: Final[str] = "loop-ab.db"

# Cat-3 bootstrap secrets the application resolves straight from the
# environment, with no config or injection path, and refuses to boot without.
# The host mints its own rather than inheriting the operator's: nothing issues a
# session, a cursor or a stored credential on this throwaway instance (its two
# reachable routes authenticate with the per-run bearer), and a secret that dies
# with the process cannot sign or decrypt anything elsewhere.
_OPAQUE_SECRET_VARS: Final[tuple[str, ...]] = (
    "SYNTHORG_JWT_SECRET",
    "SYNTHORG_PAGINATION_CURSOR_SECRET",
)
#: These two must be Fernet-shaped (URL-safe base64 of 32 raw bytes).
_FERNET_KEY_VARS: Final[tuple[str, ...]] = (
    "SYNTHORG_MASTER_KEY",
    "SYNTHORG_SETTINGS_KEY",
)
_SECRET_BYTES: Final[int] = 32


@dataclass(frozen=True)
class LoopAbHostConfig:
    """What the recording host is stood up with.

    Attributes:
        company_config: The recording company config. Its ``providers`` block
            is what the gateway resolves a run bearer's bound provider against,
            so the manifest's tiers must name providers present here.
        scratch_dir: Directory for the throwaway database, removed on exit.
        bind_host: Interface to listen on.
        bind_port: Port to listen on; ``0`` takes an ephemeral one.
        container_host: Host the sandbox addresses the recorder by.
        openhands_image: Overrides ``tools.openhands_image`` when set, so a
            maintainer can record against a locally built image.
    """

    company_config: RootConfig
    scratch_dir: Path
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = 0
    container_host: str = DEFAULT_CONTAINER_HOST
    openhands_image: str | None = None


class LoopAbGatewayHost:
    """Boots, serves and tears down the recorder's own application instance.

    Used as an async context manager: the matrix runs inside it, and every
    collaborator that needs the signer, the endpoints or the cost ledger reads
    them off the started host.
    """

    def __init__(self, config: LoopAbHostConfig) -> None:
        self._config = config
        self._app: Litestar | None = None
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self._serving: asyncio.Task[None] | None = None
        self._port: int = 0
        self._prior_secrets: dict[str, str | None] = {}

    async def __aenter__(self) -> Self:
        """Start the host, unwinding a partial start rather than leaking it.

        A boot that fails midway has already bound a socket and swapped the
        process environment, and ``__aexit__`` never runs for a ``__aenter__``
        that raised, so the teardown is driven here instead.

        Returns:
            The started host.
        """
        try:
            await self.start()
        except BaseException:
            await self.stop()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop the host, whatever ended the matrix."""
        del exc_type, exc, traceback
        await self.stop()

    @property
    def app_state(self) -> AppState:
        """The started application's state.

        Returns:
            The live :class:`AppState`.

        Raises:
            LoopAbGatewayUnavailableError: The host has not been started.
        """
        if self._app is None:
            msg = "loop A/B recording host was read before it was started"
            raise LoopAbGatewayUnavailableError(msg)
        state: AppState = self._app.state["app_state"]
        return state

    @property
    def signer(self) -> GatewaySigner:
        """The gateway signer this host's own gateway verifies with.

        Returns:
            The shared :class:`GatewaySigner`.

        Raises:
            LoopAbGatewayUnavailableError: The host is unstarted, or booted
                without a gateway. Building one here would recreate exactly the
                second-instance bug this host exists to remove.
        """
        signer = self.app_state.slice(GatewayStateSlice).signer
        if signer is None:
            msg = (
                "the recording host booted without a gateway signer, so no "
                "bearer it mints could be verified by its own gateway"
            )
            raise LoopAbGatewayUnavailableError(msg)
        return signer

    @property
    def port(self) -> int:
        """The port the host actually bound.

        Returns:
            The bound TCP port, or ``0`` before start.
        """
        return self._port

    @property
    def local_gateway_url(self) -> str:
        """The gateway base URL the in-process native drivers dial.

        Returns:
            The loopback gateway base URL.
        """
        return f"http://127.0.0.1:{self._port}{_GATEWAY_PATH}"

    @property
    def container_gateway_url(self) -> str:
        """The gateway base URL the sandbox reaches the recorder by.

        Returns:
            The container-facing gateway base URL.
        """
        return f"http://{self._config.container_host}:{self._port}{_GATEWAY_PATH}"

    @property
    def container_mcp_url(self) -> str:
        """The credentialed-MCP base URL the sandbox reaches the recorder by.

        Returns:
            The container-facing MCP base URL (the runtime appends ``/mcp``).
        """
        return f"http://{self._config.container_host}:{self._port}{_MCP_PATH}"

    async def start(self) -> None:
        """Boot the application, serve it, and publish its endpoints."""
        self._config.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._install_ephemeral_secrets()
        self._app = create_app(
            config=self._config.company_config,
            overrides=AppOverrides(
                # The startup lifecycle connects and migrates this itself, so
                # the throwaway database needs no env var and no yoyo call here.
                persistence=SQLitePersistenceBackend(
                    SQLiteConfig(path=str(self._config.scratch_dir / _SCRATCH_DB_NAME))
                ),
                cost_tracker=CostTracker(),
            ),
        )
        await self._serve(self._app)
        await self._publish_endpoints()
        logger.info(
            EVALS_LOOP_AB_HOST_STARTED,
            port=self._port,
            gateway_base_url=self.container_gateway_url,
            mcp_base_url=self.container_mcp_url,
        )

    async def stop(self) -> None:
        """Tear the server, the application lifespan and the scratch dir down.

        Idempotent, and safe on every exit path: a matrix that raised, or whose
        awaiting coroutine was cancelled, must not leave a listening socket or
        a half-open database behind.
        """
        server, sock, serving = self._server, self._socket, self._serving
        self._server = self._socket = self._serving = None
        if server is not None and serving is not None:
            server.should_exit = True
            async with asyncio.timeout(_STOP_TIMEOUT_SECONDS):
                await serving
                if server.started:
                    await server.shutdown(sockets=[sock] if sock is not None else None)
        if sock is not None:
            sock.close()
        self._app = None
        self._port = 0
        self._restore_secrets()
        await asyncio.to_thread(
            shutil.rmtree, self._config.scratch_dir, ignore_errors=True
        )
        logger.info(EVALS_LOOP_AB_HOST_STOPPED)

    def _install_ephemeral_secrets(self) -> None:
        """Give the throwaway instance its own Cat-3 bootstrap secrets.

        The Fernet-shaped keys matter beyond satisfying a validator: supplying
        them selects the encrypted secret and settings backends rather than the
        unencrypted fallbacks, so whatever this instance does write at rest is
        encrypted under a key that dies with the process.
        """
        self._prior_secrets = {
            var: os.environ.get(var)
            for var in (*_OPAQUE_SECRET_VARS, *_FERNET_KEY_VARS)
        }
        for var in _OPAQUE_SECRET_VARS:
            os.environ[var] = secrets.token_urlsafe(_SECRET_BYTES)
        for var in _FERNET_KEY_VARS:
            os.environ[var] = base64.urlsafe_b64encode(
                secrets.token_bytes(_SECRET_BYTES)
            ).decode("ascii")

    def _restore_secrets(self) -> None:
        """Put the caller's environment back the way the host found it."""
        for var, prior in self._prior_secrets.items():
            if prior is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prior
        self._prior_secrets = {}

    async def _serve(self, app: Litestar) -> None:
        """Bind the socket and run the application's lifespan + serving loop.

        Driven a phase at a time rather than through ``Server.serve()`` for two
        reasons. ``serve()`` installs its own signal handlers, which would turn
        a Ctrl-C during a long real-spend matrix into a quiet server stop
        instead of an interrupt the recorder sees. And awaiting ``startup()``
        directly means the endpoints are published only once the app is
        genuinely accepting, with no polling for a flag. The socket is bound
        first so an ephemeral port is knowable before anything serves on it.
        """
        config = uvicorn.Config(
            app,
            host=self._config.bind_host,
            port=self._config.bind_port,
            lifespan="on",
            # The application installs its own structlog pipeline at import;
            # letting uvicorn call dictConfig would replace its sinks.
            log_config=None,
        )
        if not config.loaded:
            config.load()
        server = uvicorn.Server(config)
        server.lifespan = config.lifespan_class(config)
        sock = config.bind_socket()
        self._socket = sock
        self._port = sock.getsockname()[1]
        # Raises SystemExit when the application's lifespan startup fails, so a
        # host that could not boot never reports a port it is not serving on.
        await server.startup(sockets=[sock])
        self._server = server
        self._serving = asyncio.create_task(server.main_loop())

    async def _publish_endpoints(self) -> None:
        """Write the endpoint settings the loop wiring reads.

        These go to the database tier, which outranks the environment and the
        code default, because the wiring resolves them through the settings
        resolver rather than from this object. Neither carries a write
        guardrail: the surfaces they address ship enabled already, so nothing
        here weakens a posture an operator chose.
        """
        settings = settings_service_of(self.app_state)
        await settings.set("providers", "gateway_base_url", self.container_gateway_url)
        await settings.set("tools", "credentialed_mcp_base_url", self.container_mcp_url)
        if self._config.openhands_image is not None:
            await settings.set("tools", "openhands_image", self._config.openhands_image)


__all__ = [
    "DEFAULT_BIND_HOST",
    "DEFAULT_CONTAINER_HOST",
    "LoopAbGatewayHost",
    "LoopAbHostConfig",
]

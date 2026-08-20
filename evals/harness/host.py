# module-kind: orchestrator
"""The recording host: a harness serving its own LLM gateway.

An agent inside a container authenticates to the gateway with a per-run bearer
minted by the *same* :class:`~synthorg.llm.gateway_token.GatewaySigner` instance
the gateway verifies with, and that instance is built per process and never
persisted, so only a process that owns the signer can mint a bearer its own
gateway will accept. A recorder therefore boots the real application against a
scratch database, serves it on a local port, and reads the signer off the state
the boot wiring populated. Borrowing a running backend is not a shortcut here;
it is the one configuration that cannot work.

Owning the process rather than dialling one has two further consequences every
artifact recorded through it depends on. The gateway's cost ledger belongs to
the recorder, so spend from calls made inside a container (rather than by the
engine) is visible at all. And the credentialed-MCP surface an embedded harness
insists on is the real one, served under the shipped empty capability grant, so
the handshake completes while reaching no credentialed tool.

Serving the real application means serving *all* of it, which two things here
exist to contain. ``/auth/setup`` is deliberately excluded from authentication
so an operator can never lock themselves out, and it hands a CEO session to
whoever asks first while no CEO exists, so this host seeds one of its own before
anything can accept a connection. And the listener resolves the narrowest
address the sandbox can still reach (see :mod:`evals.harness.bind_host`) rather
than every interface, which keeps the remaining surfaces (login, health, docs)
off the network. Plain HTTP is sound at that point because both resolved
addresses (host loopback, or the Docker bridge gateway) are host-local: nothing
on a shared segment is in a position to read a bearer off the wire.

The scratch database and the bootstrap secrets die with the run; the signer
never leaves memory. Per-run workspace trees live outside this module and are
reclaimed by whichever harness created them.
"""

import asyncio
import base64
import os
import secrets
import shutil
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Final, Self

import aiodocker
import uvicorn
from litestar import Litestar

from evals.errors import (
    HarnessGatewayUnavailableError,
    HarnessHostAlreadyStartedError,
    HarnessHostConfigInvalidError,
)
from evals.harness.bind_host import resolve_bind_host
from evals.harness.transcript import ASGIApp, TranscriptRecorder, transcribing
from evals.runner.execution import seed_eval_project
from synthorg.api.app import create_app
from synthorg.api.app_overrides import AppOverrides
from synthorg.api.auth.service import AuthService
from synthorg.api.gateway.state import GatewayStateSlice
from synthorg.api.state import AppState
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.llm.gateway_token import GatewaySigner
from synthorg.observability import get_logger
from synthorg.observability.events.evals import (
    EVALS_HARNESS_HOST_ADMIN_SEEDED,
    EVALS_HARNESS_HOST_IMAGES_INSTALLED,
    EVALS_HARNESS_HOST_SECRETS_INSTALLED,
    EVALS_HARNESS_HOST_START_FAILED,
    EVALS_HARNESS_HOST_STARTED,
    EVALS_HARNESS_HOST_STOP_TIMED_OUT,
    EVALS_HARNESS_HOST_STOPPED,
    EVALS_HARNESS_IMAGE_UNRESOLVED,
)
from synthorg.observability.redaction import safe_error_description
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.settings.state import config_resolver_of, settings_service_of
from synthorg.tools.sandbox._image_resolution import (
    set_resolved_sandbox_image,
    set_resolved_sidecar_image,
)

logger = get_logger(__name__)

#: Mounted route prefixes, matching what the two controllers declare.
_GATEWAY_PATH: Final[str] = "/api/v1/gateway/v1"
_MCP_PATH: Final[str] = "/api/v1/mcp-gateway"

#: Where the OpenHands container reaches the recorder from. The container joins
#: the sidecar's network namespace, so its loopback is the sidecar's own; the
#: wiring gives the sidecar this alias and nothing else resolves.
DEFAULT_CONTAINER_HOST: Final[str] = "host.docker.internal"

#: Turn extensions granted during a recording. Zero, because only the native
#: leg can earn them: the brief's ceiling is what both loops are compared on.
_NO_TURN_EXTENSIONS: Final[int] = 0

#: How long the serving task gets to unwind before teardown stops waiting on it.
#: Bounded because an in-flight request the container will never collect (its
#: sandbox was already killed) would otherwise hold a graceful shutdown open for
#: as long as the connection lives, stranding the run after its last cell.
_STOP_TIMEOUT_SECONDS: Final[float] = 30.0

#: How long a cancelled serving task gets to unwind. Short because by this
#: point the graceful shutdown has already been given its full budget and the
#: socket is about to close under it either way.
_CANCEL_TIMEOUT_SECONDS: Final[float] = 5.0

#: Label a harness gets when it does not name itself.
DEFAULT_RECORDING_LABEL: Final[str] = "recording"

#: Owner-only, because the scratch database holds this run's cost, task and
#: audit rows in the clear (only settings values are encrypted at rest), and a
#: shared CI runner is exactly where that matters.
_SCRATCH_DIR_MODE: Final[int] = 0o700

_MAX_PORT: Final[int] = 65535

#: Bytes behind the throwaway account that occupies the single-CEO slot. It
#: exists so the unauthenticated first-run setup route has nothing left to
#: grant, so its password is random, never disclosed and never used to log in.
_SEED_PASSWORD_BYTES: Final[int] = 32

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

#: ``tools.sandbox_image`` and ``tools.sidecar_image`` are compose-set: a
#: container was created against the resolved image, so the settings layer
#: refuses a write and the environment is the only capability above the code default.
#: Installed before the lifespan runs, because that is when the resolver seeds
#: the process-wide image cache every later ``DockerSandboxConfig`` reads from.
_SANDBOX_IMAGE_VAR: Final[str] = "SYNTHORG_SANDBOX_IMAGE"
_SIDECAR_IMAGE_VAR: Final[str] = "SYNTHORG_SIDECAR_IMAGE"

#: One host per process, because the ephemeral secrets live in ``os.environ``:
#: a second host would capture the first's throwaway values as the ones to put
#: back, and the first to stop would restore secrets that no longer mean
#: anything to the one still serving.
_ACTIVE_HOSTS: Final[set[int]] = set()


async def _image_id(docker: aiodocker.Docker, reference: str) -> str | None:
    """Resolve *reference* to the image id the daemon holds it against.

    Reported rather than raised when the daemon does not know the reference:
    a recording that names an absent image fails on its first container with a
    message about that container, which is a better place to learn it than a
    provenance stamp. What must not happen is a tag recorded as if it were an
    identity, so an unresolved reference is stamped as unresolved.

    Args:
        docker: A connected daemon client.
        reference: The image reference to resolve.

    Returns:
        The ``sha256:``-prefixed image id, or ``None`` when the daemon holds no
        image under *reference*.
    """
    try:
        inspected = await docker.images.inspect(reference)
    except (aiodocker.DockerError, OSError) as exc:
        logger.warning(
            EVALS_HARNESS_IMAGE_UNRESOLVED,
            image=reference,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    image_id = inspected.get("Id")
    return image_id if isinstance(image_id, str) and image_id else None


async def _cancel_serving(serving: asyncio.Task[None], port: int) -> None:
    """Stop a serving task that outlasted its graceful shutdown.

    ``asyncio.timeout`` cancels the coroutine that is waiting, never the task
    it waits on, so a timeout alone leaves the server running: still accepting,
    now against a socket the caller closes moments later, and never awaited by
    anyone. Cancellation is requested here and given its own short budget.

    Args:
        serving: The uvicorn serve task.
        port: The port it was bound to, for the report.
    """
    serving.cancel()
    try:
        async with asyncio.timeout(_CANCEL_TIMEOUT_SECONDS):
            await serving
    except asyncio.CancelledError:
        if not serving.cancelled():
            raise
    except TimeoutError:
        logger.warning(
            EVALS_HARNESS_HOST_STOP_TIMED_OUT,
            timeout_seconds=_CANCEL_TIMEOUT_SECONDS,
            port=port,
            phase="cancel",
        )


@dataclass(frozen=True)
class RecordingHostConfig:
    """What the recording host is stood up with.

    Attributes:
        company_config: The recording company config. Its ``providers`` block
            is what the gateway resolves a run bearer's bound provider against,
            so the manifest's capabilities must name providers present here.
        scratch_dir: Directory for the throwaway database, removed on exit.
        label: Names this recording, and through it the throwaway database and
            the seeded CEO account. Two harnesses recording at once would
            otherwise write the same filename under a shared scratch root and
            each read the other's rows.
        bind_host: Interface to listen on, or ``None`` to resolve the narrowest
            address the sandbox can still reach.
        bind_port: Port to listen on; ``0`` takes an ephemeral one.
        container_host: Host the sandbox addresses the recorder by.
        openhands_image: Overrides ``tools.openhands_image`` when set, so a
            maintainer can record against a locally built image.
        sandbox_image: Overrides ``tools.sandbox_image``, the image the native
            legs' shell tool runs in.
        sidecar_image: Overrides ``tools.sidecar_image``, the egress-filtering
            sidecar the OpenHands leg's pinned network needs.

    All three image overrides exist for the same reason: nothing under
    ``synthorg.tools.sandbox`` pulls, the registered defaults track the running
    version, and a recording is worth nothing if it cannot say which images the
    two legs actually ran on.
    """

    company_config: RootConfig
    scratch_dir: Path
    label: str = DEFAULT_RECORDING_LABEL
    bind_host: str | None = None
    bind_port: int = 0
    container_host: str = DEFAULT_CONTAINER_HOST
    openhands_image: str | None = None
    sandbox_image: str | None = None
    sidecar_image: str | None = None

    def __post_init__(self) -> None:
        """Reject a port the socket layer could only refuse later.

        Raises:
            HarnessHostConfigInvalidError: ``bind_port`` is outside the TCP port
                range.
        """
        if not 0 <= self.bind_port <= _MAX_PORT:
            msg = f"bind_port must be between 0 and {_MAX_PORT}, got {self.bind_port}"
            raise HarnessHostConfigInvalidError(msg)


@dataclass(frozen=True)
class RecordedImages:
    """The container images a recording actually ran its two legs on.

    Carried as one value because the three are resolved together, at one moment
    (after the application lifespan has run, so the resolver's DB > env > YAML >
    default chain has been applied), and are reported together in provenance. A
    partially-populated set would let a scoreboard name one leg's image and
    guess at the other's.

    Each reference travels with the image id the daemon resolved it to, because
    a reference alone can be a mutable tag: ``synthorg-sidecar:dev`` names a
    different image next week, and a scoreboard carrying only that cannot be
    reproduced or even checked. The id is what a later reader verifies against.

    An id is ``None`` when the daemon holds no image under that reference. That
    is reported rather than raised, because the run fails on its first
    container with a message about the container; what must not happen is a
    mutable tag recorded as though it were an identity.

    Attributes:
        sandbox: Image the native legs' shell tool runs in.
        sidecar: Image the egress-filtering sidecar runs in.
        openhands: Image the OpenHands loop's run container runs in.
        sandbox_id: Resolved image id for *sandbox*, or ``None``.
        sidecar_id: Resolved image id for *sidecar*, or ``None``.
        openhands_id: Resolved image id for *openhands*, or ``None``.
    """

    sandbox: str
    sidecar: str
    openhands: str
    sandbox_id: str | None
    sidecar_id: str | None
    openhands_id: str | None


class RecordingGatewayHost:
    """Boots, serves and tears down the recorder's own application instance.

    Used as an async context manager: the matrix runs inside it, and every
    collaborator that needs the signer, the endpoints or the cost ledger reads
    them off the started host.
    """

    def __init__(self, config: RecordingHostConfig) -> None:
        self._config = config
        self._app: Litestar | None = None
        #: Records every completion exchange of whichever cell is bound, which
        #: is the only symmetric view of the two loops: one keeps its messages
        #: in-process and the other reasons inside a container.
        self.transcripts = TranscriptRecorder()
        self._server: uvicorn.Server | None = None
        self._socket: socket.socket | None = None
        self._serving: asyncio.Task[None] | None = None
        self._port: int = 0
        self._prior_env: dict[str, str | None] = {}
        self._persistence: SQLitePersistenceBackend | None = None
        self._images: RecordedImages | None = None

    async def __aenter__(self) -> Self:
        """Start the host.

        Returns:
            The started host.
        """
        await self.start()
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
            HarnessGatewayUnavailableError: The host has not been started.
        """
        if self._app is None:
            msg = "loop A/B recording host was read before it was started"
            raise HarnessGatewayUnavailableError(msg)
        state: AppState = self._app.state["app_state"]
        return state

    @property
    def signer(self) -> GatewaySigner:
        """The gateway signer this host's own gateway verifies with.

        Returns:
            The shared :class:`GatewaySigner`.

        Raises:
            HarnessGatewayUnavailableError: The host is unstarted, or booted
                without a gateway. Building one here would recreate exactly the
                second-instance bug this host exists to remove.
        """
        signer = self.app_state.slice(GatewayStateSlice).signer
        if signer is None:
            msg = (
                "the recording host booted without a gateway signer, so no "
                "bearer it mints could be verified by its own gateway"
            )
            raise HarnessGatewayUnavailableError(msg)
        return signer

    @property
    def project_repo(self) -> ProjectRepository:
        """The repository the benchmark project was seeded into.

        Returns:
            The started host's project repository.

        Raises:
            HarnessGatewayUnavailableError: The host has not been started, so
                there is no connected backend to read from.
        """
        if self._persistence is None:
            msg = (
                "the recording host's project repository was read before it was started"
            )
            raise HarnessGatewayUnavailableError(msg)
        return self._persistence.projects

    @property
    def images(self) -> RecordedImages:
        """The images this recording resolved for its two legs.

        Returns:
            The resolved :class:`RecordedImages`.

        Raises:
            HarnessGatewayUnavailableError: The host has not been started, so
                the resolver chain that decides these has not run.
        """
        if self._images is None:
            msg = (
                "the recording host's images were read before it was started; "
                "they are resolved by the application lifespan, not before it"
            )
            raise HarnessGatewayUnavailableError(msg)
        return self._images

    @property
    def sandbox_image(self) -> str:
        """The image the native legs' shell tool runs in.

        Returns:
            The resolved sandbox image reference.
        """
        return self.images.sandbox

    @property
    def sidecar_image(self) -> str:
        """The image the egress-filtering sidecar runs in.

        Returns:
            The resolved sidecar image reference.
        """
        return self.images.sidecar

    @property
    def port(self) -> int:
        """The port the host actually bound.

        Returns:
            The bound TCP port, or ``0`` before start.
        """
        return self._port

    @property
    def serving(self) -> asyncio.Task[None] | None:
        """The task running the server's accept loop.

        Exposed so a caller can race a long matrix against it. A serving task
        that dies mid-run turns every remaining cell into a connection error
        recorded as that loop's unavailable row, which spends real money to
        measure nothing; whoever drives the matrix needs to be able to see that
        happen rather than learn it at teardown.

        Returns:
            The serving task, or ``None`` before start.
        """
        return self._serving

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
        """Boot the application, serve it, and publish its endpoints.

        A boot that fails midway has already claimed the process's one host
        slot and swapped the process environment, so it unwinds itself rather
        than leaving either for the next host. The guarantee lives here, not in
        ``__aenter__``, because a direct caller is owed it just as much and
        ``__aexit__`` never runs for a ``__aenter__`` that raised.

        Raises:
            HarnessHostAlreadyStartedError: This host, or another in the same
                process, is already holding the ephemeral bootstrap secrets.
        """
        if self._app is not None or _ACTIVE_HOSTS:
            msg = (
                "a loop A/B recording host is already started in this process; "
                "stop it before starting another"
            )
            raise HarnessHostAlreadyStartedError(msg)
        _ACTIVE_HOSTS.add(id(self))
        try:
            await self._boot()
        except BaseException as exc:
            logger.warning(
                EVALS_HARNESS_HOST_START_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self.stop()
            raise

    async def _boot(self) -> None:
        """Run the fallible boot sequence the host slot has been claimed for."""
        self._config.scratch_dir.mkdir(
            parents=True, exist_ok=True, mode=_SCRATCH_DIR_MODE
        )
        self._install_ephemeral_secrets()
        self._install_image_overrides()
        # Connected and migrated here rather than left to the startup lifecycle
        # (which does both, idempotently) so the admin seed below has a schema
        # to write into before anything can accept a connection.
        # Held so teardown can close it on the paths the application lifespan
        # never owns it: a boot that fails before ``create_app``, or one whose
        # lifespan never started. An open handle also blocks the scratch tree's
        # removal on Windows, so the leak would show up as a stranded database.
        persistence = SQLitePersistenceBackend(
            SQLiteConfig(
                path=str(self._config.scratch_dir / f"{self._config.label}.db")
            )
        )
        self._persistence = persistence
        await persistence.connect()
        await persistence.migrate()
        await self._seed_admin(persistence)
        # Every brief expects artifacts, which makes every cell a work task, and
        # the engine refuses to run one against a project it cannot look up.
        await seed_eval_project(persistence.projects)
        self._app = create_app(
            config=self._config.company_config,
            overrides=AppOverrides(
                persistence=persistence,
                cost_tracker=CostTracker(),
            ),
        )
        # Served through the tap, not around it: the recorder needs the request
        # and response bodies of every completion, and this is the only place
        # both legs are observable at all. ``self._app`` stays the Litestar the
        # rest of the host reads state off.
        await self._serve(transcribing(self._app, self.transcripts))
        await self._publish_endpoints()
        images = await self._resolve_images()
        self._images = images
        logger.info(
            EVALS_HARNESS_HOST_STARTED,
            port=self._port,
            gateway_base_url=self.container_gateway_url,
            mcp_base_url=self.container_mcp_url,
            sandbox_image=images.sandbox,
            sidecar_image=images.sidecar,
            openhands_image=images.openhands,
        )

    async def stop(self) -> None:
        """Tear the server, the application lifespan and the scratch dir down.

        Idempotent, and safe on every exit path: a matrix that raised, or whose
        awaiting coroutine was cancelled, must not leave a listening socket, a
        half-open database or the operator's environment holding this run's
        throwaway secrets. A graceful shutdown that overruns is reported and
        then abandoned, because none of that cleanup is contingent on it.
        """
        server, sock, serving, port = (
            self._server,
            self._socket,
            self._serving,
            self._port,
        )
        self._server = self._socket = self._serving = None
        try:
            if server is not None and serving is not None:
                server.should_exit = True
                try:
                    async with asyncio.timeout(_STOP_TIMEOUT_SECONDS):
                        await serving
                        if server.started:
                            await server.shutdown(
                                sockets=[sock] if sock is not None else None
                            )
                except TimeoutError:
                    logger.warning(
                        EVALS_HARNESS_HOST_STOP_TIMED_OUT,
                        timeout_seconds=_STOP_TIMEOUT_SECONDS,
                        port=port,
                    )
                    await _cancel_serving(serving, port)
        finally:
            if sock is not None:
                sock.close()
            persistence, self._persistence = self._persistence, None
            if persistence is not None:
                # A no-op once the lifespan shutdown above has closed it, and
                # the only close there is on a boot that never got that far.
                await persistence.disconnect()
            self._app = None
            self._port = 0
            self._images = None
            self._restore_env()
            # The lifespan seeded a process-wide cache describing an instance
            # that no longer exists, and every ``DockerSandboxConfig`` built
            # afterwards reads from it. Clearing it is the same leak-prevention
            # the environment restore above performs.
            set_resolved_sandbox_image(None)
            set_resolved_sidecar_image(None)
            _ACTIVE_HOSTS.discard(id(self))
            await asyncio.to_thread(
                shutil.rmtree, self._config.scratch_dir, ignore_errors=True
            )
            logger.info(EVALS_HARNESS_HOST_STOPPED, port=port)

    async def _seed_admin(self, persistence: SQLitePersistenceBackend) -> None:
        """Occupy the single-CEO slot before the host can accept a connection.

        ``POST /auth/setup`` is force-excluded from authentication so a real
        deployment cannot lock its operator out, and it grants CEO and OWNER to
        whoever reaches it first while no CEO exists. A fresh scratch database
        has none, so without this every recording run would offer an
        unauthenticated route to full control of a process holding the
        operator's real provider credentials. Seeding one closes that route by
        its own precondition, independently of which interface is bound.

        Args:
            persistence: The connected, migrated scratch backend.
        """
        auth = AuthService(self._config.company_config.api.auth)
        # Random and never disclosed: this account exists to be present, not to
        # be logged in as, and the run needs no human at the console.
        password_hash = await auth.hash_password(
            secrets.token_urlsafe(_SEED_PASSWORD_BYTES)
        )
        now = datetime.now(UTC)
        username = f"{self._config.label}-recorder"
        await persistence.users.save(
            User(
                id=str(uuid.uuid4()),
                username=username,
                password_hash=password_hash,
                role=HumanRole.CEO,
                must_change_password=False,
                org_roles=(OrgRole.OWNER,),
                created_at=now,
                updated_at=now,
            )
        )
        logger.info(EVALS_HARNESS_HOST_ADMIN_SEEDED, username=username)

    def _install_ephemeral_secrets(self) -> None:
        """Give the throwaway instance its own Cat-3 bootstrap secrets.

        The Fernet-shaped keys matter beyond satisfying a validator: supplying
        them selects the encrypted secret and settings backends rather than the
        unencrypted fallbacks, so whatever this instance does write at rest is
        encrypted under a key that dies with the process.
        """
        for var in (*_OPAQUE_SECRET_VARS, *_FERNET_KEY_VARS):
            self._prior_env.setdefault(var, os.environ.get(var))
        for var in _OPAQUE_SECRET_VARS:
            os.environ[var] = secrets.token_urlsafe(_SECRET_BYTES)
        for var in _FERNET_KEY_VARS:
            os.environ[var] = base64.urlsafe_b64encode(
                secrets.token_bytes(_SECRET_BYTES)
            ).decode("ascii")
        logger.debug(
            EVALS_HARNESS_HOST_SECRETS_INSTALLED,
            variables=(*_OPAQUE_SECRET_VARS, *_FERNET_KEY_VARS),
        )

    def _install_image_overrides(self) -> None:
        """Put the operator's chosen sandbox / sidecar images on the resolver.

        Both settings are compose-set, so the settings layer refuses a write and
        the environment is the only capability above the code default. This has to run
        before the lifespan, because that is where the resolver seeds the
        process-wide image cache every later ``DockerSandboxConfig`` reads from,
        and a value arriving after it would be resolved by nothing.
        """
        chosen = {
            _SANDBOX_IMAGE_VAR: self._config.sandbox_image,
            _SIDECAR_IMAGE_VAR: self._config.sidecar_image,
        }
        applied: dict[str, str] = {}
        for var, value in chosen.items():
            if value is None:
                continue
            self._prior_env.setdefault(var, os.environ.get(var))
            os.environ[var] = value
            applied[var] = value
        # Logged like its sibling secrets installer: the images decide what
        # every container of the recording actually runs, and an override that
        # silently did not take is a matrix measured against the wrong build.
        logger.debug(EVALS_HARNESS_HOST_IMAGES_INSTALLED, overrides=applied)

    def _restore_env(self) -> None:
        """Put the caller's environment back the way the host found it."""
        for var, prior in self._prior_env.items():
            if prior is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = prior
        self._prior_env = {}

    async def _resolve_images(self) -> RecordedImages:
        """Read back the images this instance will actually run containers on.

        Read through the application's own resolver rather than off the config,
        so an image the operator did not override is reported as the running
        instance resolved it (DB, then environment, then the code default)
        rather than as ``None``. That is what makes the scoreboard able to name
        both legs' images, including the ones nobody chose.

        Each reference is then resolved against the daemon, because a tag is
        mutable: the reference says what was asked for and the id says what
        answered, and only the second survives the tag moving.

        Returns:
            The resolved :class:`RecordedImages`.
        """
        resolver = config_resolver_of(self.app_state)
        sandbox = await resolver.get_str("tools", "sandbox_image")
        sidecar = await resolver.get_str("tools", "sidecar_image")
        openhands = await resolver.get_str("tools", "openhands_image")
        async with aiodocker.Docker() as docker:
            return RecordedImages(
                sandbox=sandbox,
                sidecar=sidecar,
                openhands=openhands,
                sandbox_id=await _image_id(docker, sandbox),
                sidecar_id=await _image_id(docker, sidecar),
                openhands_id=await _image_id(docker, openhands),
            )

    async def _serve(self, app: ASGIApp) -> None:
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
            host=await resolve_bind_host(self._config.bind_host),
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

        These go to the database capability, which outranks the environment and the
        code default, because the wiring resolves them through the settings
        resolver rather than from this object. Neither carries a write
        guardrail: the surfaces they address ship enabled already, so nothing
        here weakens a posture an operator chose.

        The turn-extension allowance is zeroed for the same reason the images
        are pinned: only one leg can use it. The native loop earns further turn
        budgets while it is still calling tools, and the OpenHands harness is
        capped at whatever it was handed with no equivalent, so a recording
        that leaves extensions on gives one loop up to four times the ceiling
        the other gets. Measured, that was 7 of 27 native sessions running past
        the brief's ceiling, one of them by 3.8x, against 0 of 27. The brief's
        ceiling is the comparison; extensions are a production behaviour that
        has no counterpart to compare against.
        """
        settings = settings_service_of(self.app_state)
        await settings.set("providers", "gateway_base_url", self.container_gateway_url)
        await settings.set("tools", "credentialed_mcp_base_url", self.container_mcp_url)
        await settings.set("engine", "max_turn_extensions", str(_NO_TURN_EXTENSIONS))
        if self._config.openhands_image is not None:
            await settings.set("tools", "openhands_image", self._config.openhands_image)


__all__ = [
    "DEFAULT_CONTAINER_HOST",
    "DEFAULT_RECORDING_LABEL",
    "RecordedImages",
    "RecordingGatewayHost",
    "RecordingHostConfig",
]

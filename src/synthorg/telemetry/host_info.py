"""Docker daemon enrichment for telemetry startup events.

Queries the Docker daemon's ``/info`` endpoint (via the same
``aiodocker`` client the sandbox layer uses) so operators can
distinguish deployments by host OS / Docker version / storage
driver / GPU capability without joining on a separate system. The
socket lives at ``/var/run/docker.sock``, which the
``compose.override.yml`` sandbox overlay bind-mounts into the
backend container. In the no-sandbox configuration the socket is
absent and every fetch path degrades to a single
``docker_info_available=False`` marker so the deployment still
shows up in telemetry without looking broken.

GPU inventory (model names, VRAM, driver version) is intentionally
**not** probed from the backend. The backend runtime image is an
apko-composed distroless Wolfi base (no shell, no package manager,
no NVIDIA tooling), and the ``compose.yml`` topology scopes GPU
access to the ``fine-tune`` service only. ``nvidia-smi`` is injected
by the NVIDIA Container Toolkit at launch time -- only into
containers that request GPUs. Running the probe from the backend
would emit ``no_nvidia_smi`` on every deployment. The achievable
backend-side GPU signal is the host-capability flag
``docker_gpu_runtime_nvidia_available`` derived from
``/info.Runtimes``; AMD and Intel GPUs do not register a Docker
runtime so they are undetectable from here.

Only a hand-picked subset of ``/info`` keys is exported. The raw
response includes host machine names, container IDs, labels, and
swarm cluster membership details that would leak private data
through the telemetry channel. Keep this module's allowlist and
the :mod:`synthorg.telemetry.privacy` scrubber's allowlist in
sync -- both are the scrub surface.
"""

import asyncio
import os

# ``Mapping`` is kept as a runtime import (rather than under
# ``TYPE_CHECKING``) so it is resolvable by
# :func:`typing.get_type_hints` on ``_extract`` under PEP 649 lazy
# annotations. Ruff would push this into the type-checking block via
# TC003 since the only reference is in a function signature, but a
# ``NameError`` at annotation-evaluation time (tests, docs, introspection
# tooling) is strictly worse than the cheap stdlib import.
from collections.abc import Mapping
from typing import Final, Literal, NotRequired, TypedDict

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import TELEMETRY_REPORT_FAILED
from synthorg.telemetry.config import MAX_STRING_LENGTH

logger = get_logger(__name__)

_DOCKER_SOCKET_PATH: Final[str] = "/var/run/docker.sock"

_DOCKER_SOCKET_STAT_TIMEOUT_SECONDS: Final[float] = 1.0
"""Upper bound on the blocking ``os.path.exists`` stat.

``asyncio.to_thread`` offloads the stat to a worker thread, but a
wedged FUSE mount or unresponsive NFS export can still hold that
thread indefinitely. Cap the wait so a stuck filesystem degrades
the startup event to ``docker_info_available=False`` instead of
leaking an orphan worker thread.
"""

_DOCKER_INFO_TIMEOUT_SECONDS: Final[float] = 5.0
"""Upper bound on the Docker ``/info`` probe.

``aiodocker`` inherits aiohttp's defaults (``sock_read=300s``), so a
wedged-but-reachable daemon could stall the startup event for five
minutes. Cap the probe so startup degrades to
``docker_info_available=False`` instead of hanging.
"""

_REASON_SOCKET_NOT_MOUNTED: Final[Literal["socket_not_mounted"]] = "socket_not_mounted"
_REASON_AIODOCKER_NOT_INSTALLED: Final[Literal["aiodocker_not_installed"]] = (
    "aiodocker_not_installed"
)
_REASON_DAEMON_UNREACHABLE: Final[Literal["daemon_unreachable"]] = "daemon_unreachable"

_NVIDIA_RUNTIME_NAME: Final[str] = "nvidia"
"""Runtime key the NVIDIA Container Toolkit registers with Docker."""


class DockerHostInfo(TypedDict):
    """Typed shape of the payload returned by :func:`fetch_docker_info`.

    ``docker_info_available`` is always present. Every other key is
    ``NotRequired`` because the success path omits
    ``docker_info_unavailable_reason`` and the unavailable path
    omits everything else. The call site merges this dict directly
    into a :class:`TelemetryEvent`'s ``properties``; keeping the
    structure a ``TypedDict`` lets mypy catch typos at the call
    site without paying the allocation cost of a concrete
    dataclass.
    """

    docker_info_available: bool
    docker_info_unavailable_reason: NotRequired[
        Literal[
            "socket_not_mounted",
            "aiodocker_not_installed",
            "daemon_unreachable",
        ]
    ]
    docker_server_version: NotRequired[str]
    docker_operating_system: NotRequired[str]
    docker_os_type: NotRequired[str]
    docker_os_version: NotRequired[str]
    docker_architecture: NotRequired[str]
    docker_kernel_version: NotRequired[str]
    docker_storage_driver: NotRequired[str]
    docker_default_runtime: NotRequired[str]
    docker_isolation: NotRequired[str]
    docker_ncpu: NotRequired[int]
    docker_mem_total: NotRequired[int]
    docker_gpu_runtime_nvidia_available: NotRequired[bool]


def _truncate(value: object) -> str:
    """Coerce to str and truncate to the scrubber's cap.

    Returns:
        The string form of ``value``, capped at ``MAX_STRING_LENGTH``.
    """
    text = str(value)
    return text[:MAX_STRING_LENGTH]


DockerInfoUnavailableReason = Literal[
    "socket_not_mounted",
    "aiodocker_not_installed",
    "daemon_unreachable",
]


def _unavailable(reason: DockerInfoUnavailableReason) -> DockerHostInfo:
    """Produce the uniform "no daemon info" marker payload.

    Returns:
        A :class:`DockerHostInfo` suitable to merge into a
        :class:`TelemetryEvent`'s ``properties``.
        ``docker_info_available`` is always ``False`` so dashboards
        can filter the two states cleanly;
        ``docker_info_unavailable_reason`` is a categorical string
        (never a raw exception message) for grouping.
    """
    return {
        "docker_info_available": False,
        "docker_info_unavailable_reason": reason,
    }


def _extract(info: Mapping[str, object]) -> DockerHostInfo:
    """Project daemon ``/info`` into the telemetry-safe subset.

    Only keys on the hand-picked allowlist are returned. Missing
    keys are silently dropped (different Docker versions expose
    different fields). Host machine names, container IDs, and
    labels are excluded by omission, not by filtering.

    Also derives ``docker_gpu_runtime_nvidia_available`` from
    ``Runtimes`` so dashboards can split GPU-capable hosts without
    probing ``nvidia-smi`` from a container that doesn't have GPU
    access (the default backend topology). AMD / Intel GPUs do not
    register a Docker runtime, so no equivalent flag exists -- that
    gap is documented at module level.

    Returns:
        The telemetry-safe subset of daemon ``/info`` with
        ``docker_info_available=True`` and the allowlisted, truncated
        fields that were present.
    """
    result: DockerHostInfo = {"docker_info_available": True}

    str_keys: Final[tuple[tuple[str, str], ...]] = (
        ("ServerVersion", "docker_server_version"),
        ("OperatingSystem", "docker_operating_system"),
        ("OSType", "docker_os_type"),
        ("OSVersion", "docker_os_version"),
        ("Architecture", "docker_architecture"),
        ("KernelVersion", "docker_kernel_version"),
        ("Driver", "docker_storage_driver"),
        ("DefaultRuntime", "docker_default_runtime"),
        ("Isolation", "docker_isolation"),
    )
    for src, dst in str_keys:
        raw = info.get(src)
        # Reject non-str values outright -- the /info contract says
        # these fields are strings, but a future Docker daemon could
        # return a dict / list / structured payload; ``str(raw)``
        # would leak it into telemetry verbatim. Mirrors the
        # isinstance guard in the int loop below.
        if not isinstance(raw, str) or raw == "":
            continue
        result[dst] = _truncate(raw)  # type: ignore[literal-required]

    int_keys: Final[tuple[tuple[str, str], ...]] = (
        ("NCPU", "docker_ncpu"),
        ("MemTotal", "docker_mem_total"),
    )
    for src, dst in int_keys:
        raw = info.get(src)
        if isinstance(raw, bool) or not isinstance(raw, int):
            continue
        result[dst] = raw  # type: ignore[literal-required]

    runtimes = info.get("Runtimes")
    result["docker_gpu_runtime_nvidia_available"] = bool(
        isinstance(runtimes, dict) and _NVIDIA_RUNTIME_NAME in runtimes
    )

    return result


async def _probe_docker_socket() -> DockerHostInfo | None:
    """Check whether the Docker socket is mounted and reachable.

    Returns:
        A marker payload when the socket is absent or the stat call
        fails; ``None`` when the socket is present (the caller continues
        to the daemon probe). Never raises.

    ``os.path.exists`` is a blocking stat syscall; a wedged FUSE
    mount or dying NFS on ``/var/run`` could stall the loop, so it
    runs off-thread via :func:`asyncio.to_thread` and is bounded by
    :data:`_DOCKER_SOCKET_STAT_TIMEOUT_SECONDS`. A stat that does
    not return within the cap collapses to ``daemon_unreachable``
    rather than holding the startup path indefinitely.
    """
    try:
        async with asyncio.timeout(_DOCKER_SOCKET_STAT_TIMEOUT_SECONDS):
            socket_exists = await asyncio.to_thread(
                os.path.exists,
                _DOCKER_SOCKET_PATH,
            )
    except TimeoutError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_socket_stat_timeout",
            error_type=type(exc).__name__,
        )
        return _unavailable(_REASON_DAEMON_UNREACHABLE)
    except OSError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_socket_stat_failed",
            error_type=type(exc).__name__,
        )
        return _unavailable(_REASON_DAEMON_UNREACHABLE)
    if not socket_exists:
        return _unavailable(_REASON_SOCKET_NOT_MOUNTED)
    return None


def _import_aiodocker() -> object | None:
    """Import ``aiodocker`` lazily; return the module or ``None``.

    ``aiodocker`` is an optional dependency (installed alongside the
    sandbox sidecar). Returning ``None`` on ``ImportError`` lets the
    caller collapse to :data:`_REASON_AIODOCKER_NOT_INSTALLED`
    without a try/except in the orchestrator.

    Returns:
        The imported ``aiodocker`` module, or ``None`` when it is not
        installed.
    """
    try:
        import aiodocker  # type: ignore[import-untyped,unused-ignore]  # noqa: PLC0415
    except ImportError:
        logger.debug(
            TELEMETRY_REPORT_FAILED,
            detail="docker_info_aiodocker_missing",
        )
        return None
    return aiodocker


async def _probe_daemon_info(aiodocker_mod: object) -> object | None:
    """Construct a client and fetch raw daemon ``/info``.

    Returns:
        The raw daemon ``/info`` dict on success, or ``None`` on any
        failure (client construction error, timeout, daemon error,
        non-dict response). Never raises.

    The :func:`asyncio.timeout` wrapper
    caps the probe at :data:`_DOCKER_INFO_TIMEOUT_SECONDS` because
    ``aiodocker`` inherits aiohttp's 300 s ``sock_read`` default;
    a wedged-but-reachable daemon would otherwise stall startup for
    up to five minutes.
    """
    try:
        client = aiodocker_mod.Docker()  # type: ignore[attr-defined]
    except Exception as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_info_client_construction",
            error_type=type(exc).__name__,
        )
        return None

    try:
        async with asyncio.timeout(_DOCKER_INFO_TIMEOUT_SECONDS), client:
            info = await client.system.info()
    except TimeoutError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_info_fetch_timeout",
            error_type=type(exc).__name__,
        )
        return None
    except Exception as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_info_fetch_failed",
            error_type=type(exc).__name__,
        )
        return None

    if not isinstance(info, dict):
        # A daemon that responds 200 OK but with a non-dict payload
        # is a real anomaly (protocol drift, proxy injection, etc.)
        # rather than a simple unreachable-daemon case. Log the
        # observed type so operators can distinguish it from the
        # ordinary ``daemon_unreachable`` collapse in dashboards.
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="docker_info_malformed_response",
            response_type=type(info).__name__,
        )
        return None
    return info


async def fetch_docker_info() -> DockerHostInfo:
    """Fetch a telemetry-safe snapshot of Docker daemon ``/info``.

    Returns:
        The allowlisted fields with ``docker_info_available=True`` when
        the daemon responds. On every failure path (socket not
        bind-mounted, ``aiodocker`` not installed, daemon unreachable,
        daemon returned an error), the ``docker_info_available=False``
        marker with a categorical reason. The caller merges the result
        straight into a :class:`TelemetryEvent`'s ``properties``.

    Never raises: every failure is caught, logged at the
    appropriate level, and collapsed into the marker payload.
    Telemetry must not affect the main application.
    """
    socket_marker = await _probe_docker_socket()
    if socket_marker is not None:
        return socket_marker

    aiodocker_mod = _import_aiodocker()
    if aiodocker_mod is None:
        return _unavailable(_REASON_AIODOCKER_NOT_INSTALLED)

    raw_info = await _probe_daemon_info(aiodocker_mod)
    if raw_info is None:
        return _unavailable(_REASON_DAEMON_UNREACHABLE)

    return _extract(raw_info)  # type: ignore[arg-type]

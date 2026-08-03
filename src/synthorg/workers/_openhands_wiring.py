# module-kind: orchestrator
"""Boot wiring for the OpenHands execution loop and its auto-selection.

Builds the loop's config, its injected dependencies (the container runtime
bound to an egress-pinned sandbox, the gateway signer, endpoint URLs), and
the settings-driven :class:`AutoLoopConfig` that makes the loop selectable
per task complexity.

The dependencies are ``None`` (loop unavailable, fails loud only if
selected) unless the gateway signer and the sandbox-reachable gateway /
credentialed-MCP endpoints are all configured; the missing piece is logged
so an operator can see why the loop stayed unavailable.
"""

import functools
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlparse

from synthorg.core.task_enums import Complexity
from synthorg.engine.loop_selector import (
    DEFAULT_AUTO_LOOP_RULES,
    AutoLoopConfig,
    AutoLoopRule,
)
from synthorg.engine.openhands.config import (
    OpenHandsLoopConfig,
    OpenHandsLoopDeps,
)
from synthorg.engine.openhands.container_runtime import SandboxStreamer
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_SELECTION_RESOLVED,
    EXECUTION_LOOP_UNAVAILABLE,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_OPENHANDS_MEMORY_LIMIT: Final[str] = "2g"
_OPENHANDS_CPU_LIMIT: Final[float] = 2.0
_OPENHANDS_PIDS_LIMIT: Final[int] = 512
_DEFAULT_HTTP_PORT: Final[int] = 80
_DEFAULT_HTTPS_PORT: Final[int] = 443
# The loop container runs on the default bridge, not the compose network, so
# no service name resolves inside it. This alias is what lets the shipped
# endpoint defaults ("http://host.docker.internal:<published-port>/...") reach
# the API, and Docker Desktop's own injection is not portable to Linux Engine.
_HOST_GATEWAY_ALIAS: Final[tuple[str, ...]] = ("host.docker.internal:host-gateway",)
_MASTER_KEY: Final[str] = "tools.openhands_enabled"


async def build_openhands_loop_config(app_state: AppState) -> OpenHandsLoopConfig:
    """Build the OpenHands loop config from live settings.

    Returns:
        The frozen loop config; the per-run bearer TTL tracks
        ``providers.gateway_token_ttl_seconds`` so it matches the gateway,
        and the idle timeout tracks ``tools.openhands_idle_timeout_seconds``.
    """
    resolver = config_resolver_of(app_state)
    ttl = await resolver.get_int("providers", "gateway_token_ttl_seconds")
    idle = await resolver.get_float("tools", "openhands_idle_timeout_seconds")
    return OpenHandsLoopConfig(token_ttl_seconds=ttl, idle_timeout_seconds=idle)


async def build_openhands_loop_deps_or_none(
    app_state: AppState,
    *,
    workspace_root: Path | None = None,
) -> OpenHandsLoopDeps | None:
    """Wire the OpenHands loop dependencies when the gateway is available.

    The loop mints per-run gateway bearers with the *same* signer the
    gateway verifies with, so the signer is pulled from the gateway feature
    slice rather than built anew. The conversation factory is the container
    runtime bound to a dedicated sandbox whose egress is pinned to exactly
    the gateway + credentialed-MCP hosts. Returns ``None`` (logging the
    missing piece) when the operator turned the capability off, the signer is
    unset, or either endpoint does not resolve to a host, leaving the loop
    unavailable (it fails loud only if selected) so sandbox egress is never
    created without a host allowlist.

    Args:
        app_state: The live application state.
        workspace_root: Sandbox root to bind instead of the deployment's agent
            workspace. A caller that recreates a workspace per run (the
            inner-loop A/B harness) supplies its own; passing it here rather
            than building a second sandbox keeps the egress pin, the path
            narrowing, the host alias and the signer re-read in one place.

    Returns:
        The wired dependencies, or ``None`` when the boundary is unwired.
    """
    from synthorg.api.gateway.state import GatewayStateSlice  # noqa: PLC0415
    from synthorg.engine.openhands.container_runtime import (  # noqa: PLC0415
        build_container_conversation,
    )

    resolver = config_resolver_of(app_state)
    gateway_base_url = await resolver.get_str("providers", "gateway_base_url")
    mcp_base_url = await resolver.get_str("tools", "credentialed_mcp_base_url")
    missing = _missing_pieces(
        enabled=await resolver.get_bool("tools", "openhands_enabled"),
        signer=app_state.slice(GatewayStateSlice).signer,
        gateway_host=_pinnable_host(gateway_base_url),
        mcp_host=_pinnable_host(mcp_base_url),
    )
    if missing:
        # An operator who turned the capability off is not looking at a
        # misconfiguration, so it reports at INFO with a note that matches the
        # cause. This runs on every watched-settings rebuild, most of which
        # have nothing to do with the loop.
        _log_unavailable(missing, disabled_by_choice=missing == (_MASTER_KEY,))
        return None

    timings = await _resolve_timings(resolver)
    if timings is None:
        return None
    idle, max_runtime = timings

    sandbox = await _build_openhands_sandbox(
        app_state, gateway_base_url, mcp_base_url, workspace_root=workspace_root
    )
    factory = functools.partial(
        build_container_conversation, sandbox, idle, max_runtime
    )
    # Re-read the signer rather than reusing the one read before the awaits
    # above: a gateway rebuild in that window installs a new one, and a loop
    # holding the old signer mints bearers the gateway no longer verifies.
    signer = app_state.slice(GatewayStateSlice).signer
    if signer is None:
        _log_unavailable(
            ("gateway_signer",),
            note="the gateway signer went away while the loop was being wired",
        )
        return None
    return OpenHandsLoopDeps(
        build_conversation=factory,
        signer=signer,
        gateway_base_url=gateway_base_url,
        mcp_base_url=mcp_base_url,
        clock=app_state.clock,
    )


def _pinnable_host(url: str) -> str:
    """Return the ``host:port`` this endpoint can be egress-pinned to.

    Both halves of the pin have to be derivable or the pin is not what it
    claims. A URL with no host leaves the allowlist empty, so enforcement
    never switches on. A URL with no path leaves the narrowing with nothing
    to name, and since the two endpoints share one host, the other endpoint's
    prefix would then be the only rule on it: this one would sit inside the
    host allowlist yet be refused per request. Failing closed here reports a
    misconfiguration instead of shipping an endpoint that cannot be reached.

    Returns:
        The ``host:port``, or ``""`` when either half is missing.
    """
    host = _host_port(url)
    return host if host and _url_path(url) else ""


def _log_unavailable(
    missing: tuple[str, ...],
    *,
    disabled_by_choice: bool = False,
    note: str | None = None,
) -> None:
    """Report the loop as unavailable, naming what is unmet."""
    log = logger.info if disabled_by_choice else logger.warning
    log(
        EXECUTION_LOOP_UNAVAILABLE,
        loop_type="openhands",
        missing=missing,
        note=note
        or (
            "OpenHands loop is switched off"
            if disabled_by_choice
            else "OpenHands loop stays unavailable until every piece is wired to "
            "a resolvable host (egress cannot be pinned without a host:port)"
        ),
    )


async def _resolve_timings(
    resolver: ConfigResolverProtocol,
) -> tuple[float, float] | None:
    """Resolve the idle and wall-clock caps, rejecting a cap above the bearer TTL.

    A run that outlives its bearer dies on a mid-run 401 from the gateway,
    which reads as an auth fault rather than the misconfiguration it is, so
    the pair fails closed here like every other unmet piece.

    Returns:
        ``(idle_seconds, max_runtime_seconds)``, or ``None`` when the cap is
        not below the gateway bearer TTL.
    """
    idle = await resolver.get_float("tools", "openhands_idle_timeout_seconds")
    max_runtime = await resolver.get_float("tools", "openhands_max_runtime_seconds")
    ttl = await resolver.get_int("providers", "gateway_token_ttl_seconds")
    if max_runtime >= ttl:
        logger.warning(
            EXECUTION_LOOP_UNAVAILABLE,
            loop_type="openhands",
            missing=("tools.openhands_max_runtime_seconds",),
            note="openhands_max_runtime_seconds must be below the gateway bearer "
            "TTL, or a long run outlives its token before the wall-clock cap "
            "ends it. Lower the cap or raise providers.gateway_token_ttl_seconds",
            max_runtime_seconds=max_runtime,
            token_ttl_seconds=ttl,
        )
        return None
    return idle, max_runtime


def _missing_pieces(
    *,
    enabled: bool,
    signer: object | None,
    gateway_host: str,
    mcp_host: str,
) -> tuple[str, ...]:
    """Name the unwired pieces that keep the loop unavailable.

    A blank host covers both an unset URL and a set-but-unparseable one (no
    scheme / no host), so the reported setting name points the operator at the
    value to fix in either case. An operator who turned the capability off gets
    that named as the single cause rather than a list of endpoints they never
    asked to wire.

    Returns:
        The names of the missing pieces (empty when all are wired).
    """
    if not enabled:
        return (_MASTER_KEY,)
    missing: list[str] = []
    if signer is None:
        missing.append("gateway_signer")
    if not gateway_host:
        missing.append("providers.gateway_base_url")
    if not mcp_host:
        missing.append("tools.credentialed_mcp_base_url")
    return tuple(missing)


async def _build_openhands_sandbox(
    app_state: AppState,
    gateway_base_url: str,
    mcp_base_url: str,
    *,
    workspace_root: Path | None = None,
) -> SandboxStreamer:
    """Build the egress-pinned Docker sandbox the OpenHands loop runs in.

    Egress is locked to exactly the gateway + credentialed-MCP hosts (the
    sidecar allowlist), the workspace is mounted read-write (the coding
    agent edits files), and the image bundles the SDK + tools. Returned as
    the narrow :class:`SandboxStreamer` the container runtime drives (the
    concrete :class:`DockerSandbox` is imported lazily: heavy aiodocker dep).

    Args:
        app_state: The live application state.
        gateway_base_url: The sandbox-reachable LLM gateway address.
        mcp_base_url: The sandbox-reachable credentialed-MCP address.
        workspace_root: Root to bind, defaulting to the deployment's agent
            workspace.

    Returns:
        A :class:`DockerSandbox` pinned to the OpenHands image + egress.
    """
    from synthorg.engine.workspace.state import (  # noqa: PLC0415
        agent_workspace_root_of,
    )
    from synthorg.tools.sandbox.docker_config import (  # noqa: PLC0415
        DockerSandboxConfig,
    )
    from synthorg.tools.sandbox.docker_sandbox import DockerSandbox  # noqa: PLC0415

    resolver = config_resolver_of(app_state)
    image = await resolver.get_str("tools", "openhands_image")
    allowed_hosts = _egress_allowlist(gateway_base_url, mcp_base_url)
    config = DockerSandboxConfig(
        image=image,
        network="bridge",
        allowed_hosts=allowed_hosts,
        allowed_paths=_egress_path_rules(gateway_base_url, mcp_base_url),
        extra_hosts=_HOST_GATEWAY_ALIAS,
        mount_mode="rw",
        memory_limit=_OPENHANDS_MEMORY_LIMIT,
        cpu_limit=_OPENHANDS_CPU_LIMIT,
        pids_limit=_OPENHANDS_PIDS_LIMIT,
    )
    return DockerSandbox(
        config=config,
        workspace=(
            workspace_root
            if workspace_root is not None
            else agent_workspace_root_of(app_state)
        ),
        clock=app_state.clock,
    )


def _egress_path_rules(
    gateway_base_url: str,
    mcp_base_url: str,
) -> tuple[str, ...]:
    """Derive the per-request path narrowing for the two endpoint URLs.

    Both endpoints live in the same backend process, so they share one
    ``host:port`` and a host allowlist alone would also grant every other
    route that process serves, including the ones authentication is
    deliberately excluded from. Naming each endpoint's own path prefix
    makes the sidecar refuse the rest per request.

    Returns:
        Sorted ``host:port=/prefix`` entries, one per reachable endpoint.
    """
    rules = {
        f"{_host_port(url)}={_url_path(url)}"
        for url in (gateway_base_url, mcp_base_url)
        if _host_port(url) and _url_path(url)
    }
    return tuple(sorted(rules))


def _url_path(url: str) -> str:
    """Extract the path component of a URL, without a trailing slash.

    Returns:
        The path, or ``""`` when the URL carries none.
    """
    path = urlparse(url).path.rstrip("/")
    return path if path.startswith("/") else ""


def _egress_allowlist(
    gateway_base_url: str,
    mcp_base_url: str,
) -> tuple[str, ...]:
    """Derive the ``host:port`` egress allowlist from the two endpoint URLs.

    Returns:
        The deduplicated ``host:port`` entries the sandbox may reach.
    """
    hosts = {
        _host_port(gateway_base_url),
        _host_port(mcp_base_url),
    }
    return tuple(sorted(h for h in hosts if h))


def _host_port(url: str) -> str:
    """Extract a ``host:port`` from a URL, inferring the scheme default port.

    Returns:
        The ``host:port`` string, or ``""`` when the URL has no host.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return ""
    port = parsed.port
    if port is None:
        port = _DEFAULT_HTTPS_PORT if parsed.scheme == "https" else _DEFAULT_HTTP_PORT
    return f"{host}:{port}"


async def build_auto_loop_config_or_none(
    app_state: AppState,
) -> AutoLoopConfig | None:
    """Build the settings-driven per-task loop-selection config.

    Returns ``None`` when auto-selection is off (the engine keeps its
    single static react loop). When on, the default complexity rules are
    merged with ``engine.loop_complexity_overrides`` and the
    ``engine.default_loop_type`` fallback, so an operator can route any
    complexity (or every unmatched task) to the OpenHands loop.

    Returns:
        The :class:`AutoLoopConfig`, or ``None`` when auto-selection is off.
    """
    resolver = config_resolver_of(app_state)
    if not await resolver.get_bool("engine", "loop_auto_select_enabled"):
        logger.debug(EXECUTION_LOOP_SELECTION_RESOLVED, enabled=False)
        return None
    default_loop_type = await resolver.get_str("engine", "default_loop_type")
    overrides = await resolver.get_str("engine", "loop_complexity_overrides")
    rules = _merge_complexity_rules(overrides)
    logger.debug(
        EXECUTION_LOOP_SELECTION_RESOLVED,
        enabled=True,
        default_loop_type=default_loop_type or "react",
        rule_count=len(rules),
    )
    return AutoLoopConfig(
        rules=rules,
        default_loop_type=default_loop_type or "react",
    )


def _merge_complexity_rules(overrides: str) -> tuple[AutoLoopRule, ...]:
    """Merge ``complexity:loop`` overrides over the default complexity rules.

    Args:
        overrides: Comma-separated ``complexity:loop`` pairs (validated at
            the settings boundary), or empty for the defaults.

    Returns:
        One rule per complexity, override winning over the default.
    """
    by_complexity: dict[Complexity, str] = {
        rule.complexity: rule.loop_type for rule in DEFAULT_AUTO_LOOP_RULES
    }
    for pair in (p.strip() for p in overrides.split(",") if p.strip()):
        complexity_name, _, loop_type = pair.partition(":")
        by_complexity[Complexity(complexity_name.strip())] = loop_type.strip()
    return tuple(
        AutoLoopRule(complexity=complexity, loop_type=loop_type)
        for complexity, loop_type in by_complexity.items()
    )

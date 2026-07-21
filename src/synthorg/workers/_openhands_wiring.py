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
from synthorg.observability.events.execution import EXECUTION_LOOP_UNAVAILABLE
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

_OPENHANDS_MEMORY_LIMIT: Final[str] = "2g"
_OPENHANDS_CPU_LIMIT: Final[float] = 2.0
_OPENHANDS_PIDS_LIMIT: Final[int] = 512
_DEFAULT_HTTP_PORT: Final[int] = 80
_DEFAULT_HTTPS_PORT: Final[int] = 443


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
) -> OpenHandsLoopDeps | None:
    """Wire the OpenHands loop dependencies when the gateway is available.

    The loop mints per-run gateway bearers with the *same* signer the
    gateway verifies with, so the signer is pulled from the gateway feature
    slice rather than built anew. The conversation factory is the container
    runtime bound to a dedicated sandbox whose egress is pinned to exactly
    the gateway + credentialed-MCP hosts. Returns ``None`` (logging the
    missing piece) when the signer or the sandbox-reachable endpoints are
    unset, leaving the loop unavailable (it fails loud only if selected).

    Returns:
        The wired dependencies, or ``None`` when the boundary is unwired.
    """
    from synthorg.api.gateway.state import GatewayStateSlice  # noqa: PLC0415
    from synthorg.engine.openhands.container_runtime import (  # noqa: PLC0415
        build_container_conversation,
    )

    signer = app_state.slice(GatewayStateSlice).signer
    resolver = config_resolver_of(app_state)
    gateway_base_url = await resolver.get_str("providers", "gateway_base_url")
    mcp_base_url = await resolver.get_str("tools", "credentialed_mcp_base_url")
    if signer is None or not gateway_base_url or not mcp_base_url:
        logger.warning(
            EXECUTION_LOOP_UNAVAILABLE,
            loop_type="openhands",
            missing=_missing_pieces(signer, gateway_base_url, mcp_base_url),
            note="OpenHands loop stays unavailable until every piece is wired",
        )
        return None

    idle = await resolver.get_float("tools", "openhands_idle_timeout_seconds")
    sandbox = await _build_openhands_sandbox(app_state, gateway_base_url, mcp_base_url)
    factory = functools.partial(build_container_conversation, sandbox, idle)
    return OpenHandsLoopDeps(
        build_conversation=factory,
        signer=signer,
        gateway_base_url=gateway_base_url,
        mcp_base_url=mcp_base_url,
        clock=app_state.clock,
    )


def _missing_pieces(
    signer: object | None,
    gateway_base_url: str,
    mcp_base_url: str,
) -> tuple[str, ...]:
    """Name the unwired pieces that keep the loop unavailable.

    Returns:
        The names of the missing pieces (empty when all are wired).
    """
    missing: list[str] = []
    if signer is None:
        missing.append("gateway_signer")
    if not gateway_base_url:
        missing.append("providers.gateway_base_url")
    if not mcp_base_url:
        missing.append("tools.credentialed_mcp_base_url")
    return tuple(missing)


async def _build_openhands_sandbox(
    app_state: AppState,
    gateway_base_url: str,
    mcp_base_url: str,
) -> SandboxStreamer:
    """Build the egress-pinned Docker sandbox the OpenHands loop runs in.

    Egress is locked to exactly the gateway + credentialed-MCP hosts (the
    sidecar allowlist), the workspace is mounted read-write (the coding
    agent edits files), and the image bundles the SDK + tools. Returned as
    the narrow :class:`SandboxStreamer` the container runtime drives (the
    concrete :class:`DockerSandbox` is imported lazily: heavy aiodocker dep).

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
        mount_mode="rw",
        memory_limit=_OPENHANDS_MEMORY_LIMIT,
        cpu_limit=_OPENHANDS_CPU_LIMIT,
        pids_limit=_OPENHANDS_PIDS_LIMIT,
    )
    return DockerSandbox(
        config=config,
        workspace=agent_workspace_root_of(app_state),
        clock=app_state.clock,
    )


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
        return None
    default_loop_type = await resolver.get_str("engine", "default_loop_type")
    overrides = await resolver.get_str("engine", "loop_complexity_overrides")
    rules = _merge_complexity_rules(overrides)
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

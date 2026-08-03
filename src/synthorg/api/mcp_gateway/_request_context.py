# module-kind: code
"""Per-request resolution for the credentialed-tool MCP surface.

Everything a request needs from ``AppState`` that is not transport: the
per-family kill switches the dispatch denials read, and the host-side
:class:`CredentialedToolContext` a ``tools/call`` executes against.

The context is opened lazily. Building it needs a configured forge and chat
connection, which a deployment that has wired neither cannot supply, and the
embedded harness must complete ``initialize`` before it can construct its agent
at all; resolving it eagerly therefore refused the handshake on a capability
that ships enabled. The kill switches are resolved separately because they gate
``tools/list`` as well as ``tools/call``.
"""

import asyncio
from dataclasses import dataclass
from typing import Final

from synthorg._core.features import require_service
from synthorg.api.mcp_gateway.protocol import ToolContextProvider
from synthorg.api.mcp_gateway.tools import (
    CredentialedToolContext,
    build_security_pre_check,
)
from synthorg.api.state import AppState
from synthorg.approval.state import approval_store_of
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine._security_factory import make_security_interceptor
from synthorg.engine.workspace.state import agent_workspace_root_of
from synthorg.hr.state import HrStateSlice
from synthorg.integrations.state import IntegrationsStateSlice
from synthorg.llm.gateway_token import GatewayTokenClaims
from synthorg.observability import get_logger
from synthorg.observability.events.gateway import GATEWAY_DISPATCH_FAILED
from synthorg.security.state import audit_log_of
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)

_TOOLS_NS: Final[str] = "tools"


@dataclass(frozen=True)
class _DeploySettings:
    """The per-request deploy-tool settings resolved as one unit.

    ``enabled`` is the defence-in-depth kill switch: when off, the caller
    denies every deploy tool this request regardless of the capability grant.
    """

    enabled: bool
    targets: frozenset[str]
    timeout_seconds: float
    max_log_chars: int


@dataclass(frozen=True)
class _PublishSettings:
    """The per-request publish-tool settings resolved as one unit.

    ``enabled`` is the defence-in-depth kill switch: when off, the caller
    denies every publish tool this request regardless of the capability grant.
    """

    enabled: bool
    targets: frozenset[str]
    timeout_seconds: float
    max_manifest_bytes: int
    max_image_bytes: int


@dataclass(frozen=True)
class _FamilyKillSwitches:
    """The per-family enable flags the caller threads into the dispatch denials."""

    deploy_enabled: bool
    publish_enabled: bool


@dataclass(frozen=True)
class _ResolvedContextInputs:
    """The awaited per-request reads a credentialed-tool context is built from."""

    forge_connection: str
    chat_connection: str
    forge_timeout_seconds: float
    chat_timeout_seconds: float
    forge_max_read_chars: int
    deploy_settings: _DeploySettings
    publish_settings: _PublishSettings
    actor: AgentIdentity | None


async def _resolve_deploy_settings(resolver: ConfigResolver) -> _DeploySettings:
    """Read the deploy-tool settings for this request.

    Scheduled as one task in the caller's ``TaskGroup`` so it runs alongside
    the forge / chat / actor reads; its own reads are cheap resolver-cache
    hits, so they resolve sequentially here rather than nesting a second group.

    Args:
        resolver: The per-request configuration resolver.

    Returns:
        The resolved :class:`_DeploySettings`.
    """
    enabled = await resolver.get_bool(_TOOLS_NS, "deploy_tools_enabled")
    targets = await resolver.get_str(_TOOLS_NS, "deploy_tools_targets")
    timeout = await resolver.get_float(_TOOLS_NS, "deploy_tools_timeout_seconds")
    max_log_chars = await resolver.get_int(_TOOLS_NS, "deploy_tools_max_log_chars")
    return _DeploySettings(
        enabled=enabled,
        targets=_parse_targets(targets),
        timeout_seconds=timeout,
        max_log_chars=max_log_chars,
    )


async def _resolve_publish_settings(resolver: ConfigResolver) -> _PublishSettings:
    """Read the publish-tool settings for this request.

    Scheduled as one task in the caller's ``TaskGroup`` alongside the other
    per-request reads.

    Args:
        resolver: The per-request configuration resolver.

    Returns:
        The resolved :class:`_PublishSettings`.
    """
    enabled = await resolver.get_bool(_TOOLS_NS, "publish_tools_enabled")
    targets = await resolver.get_str(_TOOLS_NS, "publish_tools_targets")
    timeout = await resolver.get_float(_TOOLS_NS, "publish_tools_timeout_seconds")
    max_manifest = await resolver.get_int(_TOOLS_NS, "publish_tools_max_manifest_bytes")
    max_image = await resolver.get_int(_TOOLS_NS, "publish_tools_max_image_bytes")
    return _PublishSettings(
        enabled=enabled,
        targets=_parse_targets(targets),
        timeout_seconds=timeout,
        max_manifest_bytes=max_manifest,
        max_image_bytes=max_image,
    )


async def _resolve_kill_switches(resolver: ConfigResolver) -> _FamilyKillSwitches:
    """Read the per-family enable flags on their own.

    Read here rather than out of the full context bundle because the dispatch
    denials apply to ``tools/list`` too, and the context is deferred to the one
    method that executes a tool. Both are cheap resolver-cache hits.

    Returns:
        The deploy / publish kill switches for this request.
    """
    return _FamilyKillSwitches(
        deploy_enabled=await resolver.get_bool(_TOOLS_NS, "deploy_tools_enabled"),
        publish_enabled=await resolver.get_bool(_TOOLS_NS, "publish_tools_enabled"),
    )


def _context_opener(
    app_state: AppState, *, claims: GatewayTokenClaims
) -> ToolContextProvider:
    """Build the deferred, once-per-request credentialed-tool context opener.

    Cached so a batch carrying several calls brokers its collaborators once.

    Returns:
        An awaitable factory for this request's context.
    """
    opened: CredentialedToolContext | None = None

    async def _open() -> CredentialedToolContext:
        nonlocal opened
        if opened is None:
            opened = await _build_context(
                app_state, agent_id=claims.agent_id, task_id=claims.task_id
            )
        return opened

    return _open


async def _build_context(
    app_state: AppState, *, agent_id: str, task_id: str | None
) -> CredentialedToolContext:
    """Fan out the per-request reads, then assemble the credentialed context.

    The independent reads (forge / chat settings, the deploy and publish
    settings bundles, and the actor lookup) run concurrently under one
    ``TaskGroup``; :func:`_assemble_context` turns their results into the
    context. ``agent_id`` / ``task_id`` come from the verified bearer and bind
    the security context per run.

    Returns:
        The :class:`CredentialedToolContext` for this request.
    """
    resolver = config_resolver_of(app_state)
    try:
        async with asyncio.TaskGroup() as tg:
            forge_conn = tg.create_task(
                resolver.get_str(_TOOLS_NS, "forge_tools_connection")
            )
            chat_conn = tg.create_task(
                resolver.get_str(_TOOLS_NS, "chat_tools_connection")
            )
            forge_timeout = tg.create_task(
                resolver.get_float(_TOOLS_NS, "forge_tools_timeout_seconds")
            )
            chat_timeout = tg.create_task(
                resolver.get_float(_TOOLS_NS, "chat_tools_timeout_seconds")
            )
            forge_read = tg.create_task(
                resolver.get_int(_TOOLS_NS, "forge_tools_max_read_chars")
            )
            deploy = tg.create_task(_resolve_deploy_settings(resolver))
            publish = tg.create_task(_resolve_publish_settings(resolver))
            actor = tg.create_task(_resolve_actor(app_state, agent_id=agent_id))
    except ExceptionGroup as eg:
        # Surface the first underlying error (e.g. a DomainError from a
        # settings read) so the caller's ``except DomainError`` mapping runs
        # rather than a raw ExceptionGroup escaping to the generic handler.
        reraise_critical(eg)
        if len(eg.exceptions) > 1:
            # Only the re-raised exception is otherwise logged, so a request
            # that failed for several independent reasons at once would hide
            # every cause but one.
            logger.warning(
                GATEWAY_DISPATCH_FAILED,
                surface="mcp-gateway",
                reason="concurrent_context_build_failures",
                error_types=[type(exc).__name__ for exc in eg.exceptions],
            )
        raise eg.exceptions[0] from eg
    inputs = _ResolvedContextInputs(
        forge_connection=forge_conn.result(),
        chat_connection=chat_conn.result(),
        forge_timeout_seconds=forge_timeout.result(),
        chat_timeout_seconds=chat_timeout.result(),
        forge_max_read_chars=forge_read.result(),
        deploy_settings=deploy.result(),
        publish_settings=publish.result(),
        actor=actor.result(),
    )
    return _assemble_context(app_state, inputs, agent_id=agent_id, task_id=task_id)


def _assemble_context(
    app_state: AppState,
    inputs: _ResolvedContextInputs,
    *,
    agent_id: str,
    task_id: str | None,
) -> CredentialedToolContext:
    """Assemble the context from the resolved per-request reads.

    The SecOps pre-tool screen is wired fail-closed so the rule-engine screening
    runs before every credentialed dispatch.

    Returns:
        The :class:`CredentialedToolContext` for this request.
    """
    interceptor = make_security_interceptor(
        app_state.security_runtime_config.current,
        audit_log_of(app_state),
        approval_store=approval_store_of(app_state),
    )
    deploy_settings = inputs.deploy_settings
    publish_settings = inputs.publish_settings
    return CredentialedToolContext(
        connection_catalog=require_service(
            app_state.slice(IntegrationsStateSlice).connection_catalog,
            "connection catalog",
        ),
        approval_store=approval_store_of(app_state),
        clock=app_state.clock,
        forge_connection=inputs.forge_connection,
        chat_connection=inputs.chat_connection,
        forge_timeout_seconds=inputs.forge_timeout_seconds,
        chat_timeout_seconds=inputs.chat_timeout_seconds,
        forge_max_read_chars=inputs.forge_max_read_chars,
        deploy_targets=deploy_settings.targets,
        deploy_timeout_seconds=deploy_settings.timeout_seconds,
        deploy_max_log_chars=deploy_settings.max_log_chars,
        publish_targets=publish_settings.targets,
        publish_timeout_seconds=publish_settings.timeout_seconds,
        publish_max_manifest_bytes=publish_settings.max_manifest_bytes,
        publish_max_image_bytes=publish_settings.max_image_bytes,
        workspace_root=agent_workspace_root_of(app_state),
        actor=inputs.actor,
        security_pre_check=build_security_pre_check(
            interceptor, agent_id=agent_id, task_id=task_id
        ),
    )


def _parse_targets(raw: str) -> frozenset[str]:
    """Parse the comma-separated deploy-target allowlist.

    Args:
        raw: The configured allowlist string.

    Returns:
        The set of non-blank target names. Empty allows nothing.
    """
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


async def _resolve_actor(app_state: AppState, *, agent_id: str) -> AgentIdentity | None:
    """Resolve the calling agent's identity for the destructive-op audit.

    Looked up from the registry rather than synthesised from the verified
    claim, so a token outliving its agent record cannot deploy: the
    destructive path's guardrail refuses a call it cannot attribute.

    Args:
        app_state: The live application state.
        agent_id: The caller id from the verified bearer.

    Returns:
        The resolved identity, or ``None`` when no registry is wired or
        the agent is unknown.
    """
    registry = app_state.slice(HrStateSlice).agent_registry
    if registry is None:
        return None
    return await registry.get(NotBlankStr(agent_id))

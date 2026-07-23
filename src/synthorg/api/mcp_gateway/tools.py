# module-kind: code
"""Credentialed tool registry and the governed invoke path.

Each credentialed tool wraps an existing ``GovernedConnectionTool``
(forge / chat) so the connection approval gate, action-signature binding,
credential brokering and egress pinning are reused verbatim, host-side.
The invoke path scopes visibility per actor, validates arguments, runs the
governed tool, and fences the result with ``wrap_untrusted`` before it
returns to the harness. This module holds no transport; the streamable-http
controller drives it.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock
from synthorg.core.domain_errors import (
    ForbiddenError,
    ResourceNotFoundError,
    ValidationError,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TOOL_RESULT, wrap_untrusted
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.observability import get_logger
from synthorg.observability.events.gateway import (
    GATEWAY_DISPATCH_FAILED,
    GATEWAY_TOOL_INVOKED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.protocol import SecurityInterceptionStrategy
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.chat._args import ChatDirectoryArgs, ChatMessagesArgs
from synthorg.tools.chat._runtime import ChatToolDeps, ChatToolsRuntime
from synthorg.tools.chat.chat_tools import ChatDirectoryTool, ChatMessagesTool
from synthorg.tools.deploy._args import DeployReleaseArgs, DeployRunArgs
from synthorg.tools.deploy._runtime import DeployToolDeps, DeployToolsRuntime
from synthorg.tools.deploy.deploy_tools import DeployReleaseTool, DeployRunTool
from synthorg.tools.forge._args import (
    ForgeCiArgs,
    ForgeIssueArgs,
    ForgePullRequestArgs,
    ForgeRepoArgs,
)
from synthorg.tools.forge._runtime import ForgeToolDeps, ForgeToolsRuntime
from synthorg.tools.forge.forge_tools import (
    ForgeCiTool,
    ForgeIssueTool,
    ForgePullRequestTool,
    ForgeRepoTool,
)

logger = get_logger(__name__)

_ERROR_PREFIX: Final[str] = "tool error: "

# Optional host-side security pre-check: raises to deny a call before dispatch.
type SecurityPreCheck = Callable[[str, dict[str, object]], Awaitable[None]]


class CredentialedToolContext(BaseModel):
    """Host-side collaborators the credentialed tools need per call.

    Credentials are brokered from ``connection_catalog`` inside the governed
    tool and never leave the process. The connection names bind which forge /
    chat connection every call targets; egress is pinned to that connection's
    host by construction. Frozen with validated fields so a blank connection
    or non-positive timeout / read cap fails at this governance boundary.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
    )

    connection_catalog: ConnectionCatalog
    approval_store: ApprovalStoreProtocol
    clock: Clock
    forge_connection: NotBlankStr
    chat_connection: NotBlankStr
    forge_timeout_seconds: float = Field(gt=0)
    chat_timeout_seconds: float = Field(gt=0)
    forge_max_read_chars: int = Field(gt=0)
    # Deploy targets are chosen per call from this operator-set allowlist
    # rather than bound to one connection: an organisation deploys to
    # several targets. Empty allows nothing, matching the secure default
    # of the capability grant itself.
    deploy_targets: frozenset[str] = frozenset()
    deploy_timeout_seconds: float = Field(gt=0)
    deploy_max_log_chars: int = Field(gt=0)
    # Resolved host-side from the verified token claims, never synthesised
    # from the claim string. ``None`` leaves the destructive path unable to
    # attribute the action, so its guardrail refuses the call.
    actor: AgentIdentity | None = None
    security_pre_check: SecurityPreCheck | None = None


@dataclass(frozen=True)
class _CredentialedTool:
    """A credentialed tool spec: schema, capability, and a governed builder."""

    name: str
    description: str
    capability: str
    category: ToolCategory
    args_model: type[BaseModel]
    build: Callable[[CredentialedToolContext, str], BaseTool]


def _forge_deps(ctx: CredentialedToolContext, agent_id: str) -> ForgeToolDeps:
    """Build per-call forge deps bound to *agent_id* and the forge connection.

    Returns:
        The per-call :class:`ForgeToolDeps`.
    """
    return ForgeToolDeps(
        runtime=ForgeToolsRuntime(
            connection_catalog=ctx.connection_catalog,
            connection_name=ctx.forge_connection,
            timeout_seconds=ctx.forge_timeout_seconds,
            max_read_chars=ctx.forge_max_read_chars,
        ),
        approval_store=ctx.approval_store,
        agent_id=agent_id,
        clock=ctx.clock,
    )


def _chat_deps(ctx: CredentialedToolContext, agent_id: str) -> ChatToolDeps:
    """Build per-call chat deps bound to *agent_id* and the chat connection.

    Returns:
        The per-call :class:`ChatToolDeps`.
    """
    return ChatToolDeps(
        runtime=ChatToolsRuntime(
            connection_catalog=ctx.connection_catalog,
            connection_name=ctx.chat_connection,
            timeout_seconds=ctx.chat_timeout_seconds,
        ),
        approval_store=ctx.approval_store,
        agent_id=agent_id,
        clock=ctx.clock,
    )


def _deploy_deps(ctx: CredentialedToolContext, agent_id: str) -> DeployToolDeps:
    """Build per-call deploy deps bound to *agent_id* and the target allowlist.

    Args:
        ctx: The per-request host-side collaborators.
        agent_id: The verified calling agent.

    Returns:
        The per-call :class:`DeployToolDeps`.
    """
    return DeployToolDeps(
        runtime=DeployToolsRuntime(
            connection_catalog=ctx.connection_catalog,
            allowed_targets=ctx.deploy_targets,
            timeout_seconds=ctx.deploy_timeout_seconds,
            max_log_chars=ctx.deploy_max_log_chars,
        ),
        approval_store=ctx.approval_store,
        agent_id=agent_id,
        clock=ctx.clock,
    )


CREDENTIALED_TOOLS: Final[tuple[_CredentialedTool, ...]] = (
    _CredentialedTool(
        name="forge_repo",
        description="Read forge repo metadata, a file, or a directory listing.",
        capability="forge:read",
        category=ToolCategory.VERSION_CONTROL,
        args_model=ForgeRepoArgs,
        build=lambda ctx, aid: ForgeRepoTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_issue",
        description="Read, open, or comment on forge issues (writes need approval).",
        capability="forge:write",
        category=ToolCategory.VERSION_CONTROL,
        args_model=ForgeIssueArgs,
        build=lambda ctx, aid: ForgeIssueTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_pull_request",
        description=(
            "Read, open, comment, review or merge forge pull requests "
            "(writes need approval)."
        ),
        capability="forge:write",
        category=ToolCategory.VERSION_CONTROL,
        args_model=ForgePullRequestArgs,
        build=lambda ctx, aid: ForgePullRequestTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="forge_ci",
        description="Read continuous-integration runs for the bound forge repo.",
        capability="forge:read",
        category=ToolCategory.VERSION_CONTROL,
        args_model=ForgeCiArgs,
        build=lambda ctx, aid: ForgeCiTool(deps=_forge_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="chat_messages",
        description="Send or read messages on the bound chat connection.",
        capability="chat:write",
        category=ToolCategory.COMMUNICATION,
        args_model=ChatMessagesArgs,
        build=lambda ctx, aid: ChatMessagesTool(deps=_chat_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="chat_directory",
        description="List channels or look up a user on the bound chat connection.",
        capability="chat:read",
        category=ToolCategory.COMMUNICATION,
        args_model=ChatDirectoryArgs,
        build=lambda ctx, aid: ChatDirectoryTool(deps=_chat_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="deploy_run",
        description=(
            "Read a deployment's state, list recent deployments, or fetch a "
            "deployment's logs from an allowlisted deploy target."
        ),
        capability="deploy:read",
        category=ToolCategory.DEPLOYMENT,
        args_model=DeployRunArgs,
        build=lambda ctx, aid: DeployRunTool(deps=_deploy_deps(ctx, aid)),
    ),
    _CredentialedTool(
        name="deploy_release",
        description=(
            "Trigger a release to an allowlisted deploy target. Replaces what "
            "is running; requires confirm, a reason, and human approval."
        ),
        capability="deploy:write",
        category=ToolCategory.DEPLOYMENT,
        args_model=DeployReleaseArgs,
        build=lambda ctx, aid: DeployReleaseTool(
            deps=_deploy_deps(ctx, aid), actor=ctx.actor
        ),
    ),
)

_TOOLS_BY_NAME: Final[MappingProxyType[str, _CredentialedTool]] = MappingProxyType(
    {tool.name: tool for tool in CREDENTIALED_TOOLS}
)


def _capability_matches(capability: str, patterns: tuple[str, ...]) -> bool:
    """Return whether *capability* (``domain:action``) matches any pattern.

    Patterns support ``domain:action`` (exact), ``domain:*`` (all actions in
    a domain), ``*:action`` (an action across domains) and ``*`` (everything),
    mirroring :class:`MCPToolScoper`.

    Returns:
        ``True`` when any pattern matches *capability*.
    """
    domain, _, action = capability.partition(":")
    for pattern in patterns:
        if pattern in {"*", capability}:
            return True
        p_domain, _, p_action = pattern.partition(":")
        if p_domain in {"*", domain} and p_action in {"*", action}:
            return True
    return False


def visible_tool_names(
    *,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> frozenset[str]:
    """Return the credentialed tool names visible to an actor.

    Resolution order (first match wins), mirroring :class:`MCPToolScoper`:
    an explicit ``denied`` name is excluded; an explicit ``allowed`` name is
    included; else a capability match includes it; otherwise it is excluded.
    Empty *capabilities* with no allowances grants nothing (secure default).

    Args:
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances (override capabilities).
        denied: Explicit tool-name denials (highest priority).

    Returns:
        The frozenset of visible tool names.
    """
    visible: set[str] = set()
    for spec in CREDENTIALED_TOOLS:
        if spec.name in denied:
            continue
        if spec.name in allowed or _capability_matches(spec.capability, capabilities):
            visible.add(spec.name)
    return frozenset(visible)


async def invoke_credentialed_tool(  # noqa: PLR0913 -- scope + validate + dispatch surface
    name: str,
    arguments: dict[str, object],
    *,
    ctx: CredentialedToolContext,
    agent_id: str,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> str:
    """Scope, validate, run and fence one credentialed tool call.

    Args:
        name: The tool name the harness requested.
        arguments: The raw tool arguments.
        ctx: Host-side collaborators (catalog, approval store, clock, config).
        agent_id: The calling actor's id, bound into the approval gate.
        capabilities: Capability patterns the actor is granted.
        allowed: Explicit tool-name allowances for this actor.
        denied: Explicit tool-name denials for this actor.

    Returns:
        The tool result, fenced with ``wrap_untrusted`` (or an approval-parking
        notice when a write awaits approval).

    Raises:
        ResourceNotFoundError: If *name* is not a credentialed tool.
        ForbiddenError: If the tool is not visible to the actor.
        ValidationError: If the arguments fail the typed-boundary parse.
    """
    spec = _TOOLS_BY_NAME.get(name)
    if spec is None:
        msg = f"unknown credentialed tool: {name!r}"
        raise ResourceNotFoundError(msg)
    visible = visible_tool_names(
        capabilities=capabilities, allowed=allowed, denied=denied
    )
    if spec.name not in visible:
        msg = f"tool {name!r} is not permitted for this actor"
        raise ForbiddenError(msg)
    if ctx.security_pre_check is not None:
        await ctx.security_pre_check(name, arguments)
    logger.info(GATEWAY_TOOL_INVOKED, tool=name, agent_id=agent_id, surface="mcp")
    # ``parse_typed`` re-raises pydantic's ``ValidationError``; convert it to a
    # domain ``ValidationError`` so ``protocol._tools_call``'s ``except
    # DomainError`` keeps a malformed argument inside the JSON-RPC envelope
    # (and never 500s or aborts a batch).
    try:
        parse_typed("mcp_gateway.tool", arguments, spec.args_model)
    except PydanticValidationError as exc:
        msg = f"invalid arguments for credentialed tool {name!r}"
        raise ValidationError(msg) from exc
    tool = spec.build(ctx, agent_id)
    result = await tool.execute(arguments=arguments)
    rendered = _render(result, tool=name, agent_id=agent_id)
    return wrap_untrusted(TAG_TOOL_RESULT, rendered)


def build_security_pre_check(
    interceptor: SecurityInterceptionStrategy | None,
    *,
    agent_id: str,
    task_id: str | None = None,
) -> SecurityPreCheck:
    """Build the fail-closed SecOps pre-tool screen for the credentialed path.

    The returned check runs the rule-engine screening the credentialed-MCP
    design lists as governance step 2. It is fail-closed: with no active
    security governance (``interceptor`` is ``None``) every credentialed call
    is denied, so the credentialed tools are unreachable until an operator
    enables security. A non-``ALLOW`` verdict (deny / escalate) denies the
    call.

    Args:
        interceptor: The security interception strategy, or ``None`` when
            security governance is not configured.
        agent_id: The calling actor id, bound into each security context.
        task_id: Optional task attribution for the security context.

    Returns:
        A :data:`SecurityPreCheck` that raises to deny a call.
    """

    async def _pre_check(name: str, arguments: dict[str, object]) -> None:
        spec = _TOOLS_BY_NAME.get(name)
        if interceptor is None or spec is None:
            msg = "credentialed MCP requires active security governance"
            raise ForbiddenError(msg)
        verdict = await interceptor.evaluate_pre_tool(
            SecurityContext(
                tool_name=name,
                tool_category=spec.category,
                action_type=spec.capability,
                arguments=arguments,
                agent_id=agent_id or None,
                task_id=task_id,
            )
        )
        if verdict.verdict is not SecurityVerdictType.ALLOW:
            msg = f"security governance denied credentialed tool {name!r}"
            raise ForbiddenError(msg)

    return _pre_check


def _render(result: ToolExecutionResult, *, tool: str, agent_id: str) -> str:
    """Render a tool result to text for the harness.

    Args:
        result: The governed tool's execution result.
        tool: The tool name, threaded through for error-log correlation.
        agent_id: The calling actor id, threaded through for correlation.

    Returns:
        The result content, prefixed to flag an error result.
    """
    if result.is_error:
        logger.warning(
            GATEWAY_DISPATCH_FAILED,
            surface="mcp",
            is_error=True,
            tool=tool,
            agent_id=agent_id,
        )
        return f"{_ERROR_PREFIX}{result.content}"
    return result.content

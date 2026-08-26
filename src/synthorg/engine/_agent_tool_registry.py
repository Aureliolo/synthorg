"""Per-run registry augmentation for the governed connection tools.

Companion to ``_security_factory``'s ``registry_with_*`` family, kept
separate so that module stays within its size budget. Every builder is
run-scoped: it binds the run's identity, task, and effective autonomy
onto the boot-scoped runtime bundle and appends the resulting tools to the
agent's registry, gating every write through the identity-bound approval
flow. Each returns the registry unchanged when its runtime bundle or the
approval store is absent (the feature is off, or writes could not be
gated).

The destructive tools (``deploy_release``, ``publish_push``) additionally
take the calling ``AgentIdentity`` as their audit actor. It is the identity
this augmentation was already handed, which is the strongest binding
available: the tool refuses an unattributable call before the approval gate,
so passing the run's own identity is what makes the guardrail satisfiable at
all.
"""

from typing import TYPE_CHECKING

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.engine.delegation.protocol import SubAgentRunner
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE
from synthorg.observability.events.tool import (
    CHAT_TOOL_GRANTED,
    DELEGATE_TOOL_GRANTED,
    DEPLOY_TOOL_GRANTED,
    FORGE_TOOL_GRANTED,
    GOVERNED_TOOL_WITHHELD_UNGATED,
    PUBLISH_TOOL_GRANTED,
)
from synthorg.security.risk_map import default_risk_classifier
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.base import BaseTool
from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.connection_tool_runtimes import ConnectionToolRuntimes
from synthorg.tools.forge._runtime import ForgeToolsRuntime
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.core.effective_autonomy import EffectiveAutonomy

    # Cycle breakers: both packages' ``__init__`` reach the MCP admin
    # guardrail, which imports ``api.state``, and this module is pulled in
    # while the engine is still constructing itself.
    from synthorg.tools.deploy._runtime import DeployToolsRuntime
    from synthorg.tools.publish._runtime import PublishToolsRuntime

logger = get_logger(__name__)


def _log_if_ungated(
    runtime: object | None,
    approval_store: ApprovalStoreProtocol | None,
    *,
    family: str,
    identity: AgentIdentity,
    task_id: str | None,
) -> None:
    """Report a family withheld because its writes could not be gated.

    Two conditions reach the skip and only one is ordinary. A ``None``
    runtime is the feature being off, already reported at wiring time. A
    missing approval store is not: the operator asked for the family and the
    gate that makes its writes safe to grant is absent, so the tools are
    withheld rather than handed over ungoverned. Collapsing both into one
    silent return loses exactly the interesting half.
    """
    if runtime is not None and approval_store is None:
        logger.warning(
            GOVERNED_TOOL_WITHHELD_UNGATED,
            agent_id=str(identity.id),
            task_id=task_id,
            family=family,
            note="runtime is wired but no approval store can gate its writes",
        )


def registry_with_forge_tools(
    tool_registry: ToolRegistry,
    runtime: ForgeToolsRuntime | None,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed forge agent tools when their runtime is wired.

    Returns:
        A :class:`ToolRegistry` with ``forge_repo`` / ``forge_issue`` /
        ``forge_pull_request`` / ``forge_push`` / ``forge_ci`` appended when
        both ``runtime`` and ``approval_store`` are wired; otherwise the
        original registry unchanged.
    """
    if runtime is None or approval_store is None:
        _log_if_ungated(
            runtime, approval_store, family="forge", identity=identity, task_id=task_id
        )
        return tool_registry

    from synthorg.tools.forge._runtime import ForgeToolDeps  # noqa: PLC0415
    from synthorg.tools.forge.forge_tools import (  # noqa: PLC0415
        ForgeCiTool,
        ForgeIssueTool,
        ForgePullRequestTool,
        ForgePushTool,
        ForgeRepoTool,
    )

    deps = ForgeToolDeps(
        runtime=runtime,
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
    )
    forge_tools: list[BaseTool] = [
        ForgeRepoTool(deps=deps),
        ForgeIssueTool(deps=deps),
        ForgePullRequestTool(deps=deps),
        ForgePushTool(deps=deps),
        ForgeCiTool(deps=deps),
    ]
    logger.debug(
        FORGE_TOOL_GRANTED,
        agent_id=str(identity.id),
        task_id=task_id,
        connection=runtime.connection_name,
        tools=[tool.name for tool in forge_tools],
    )
    existing = list(tool_registry.all_tools())
    return ToolRegistry([*existing, *forge_tools])


def registry_with_chat_tools(
    tool_registry: ToolRegistry,
    runtime: ChatToolsRuntime | None,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed chat agent tools when their runtime is wired.

    Returns:
        A :class:`ToolRegistry` with ``chat_messages`` / ``chat_directory``
        appended when both ``runtime`` and ``approval_store`` are wired;
        otherwise the original registry unchanged.
    """
    if runtime is None or approval_store is None:
        _log_if_ungated(
            runtime, approval_store, family="chat", identity=identity, task_id=task_id
        )
        return tool_registry

    from synthorg.tools.chat._runtime import ChatToolDeps  # noqa: PLC0415
    from synthorg.tools.chat.chat_tools import (  # noqa: PLC0415
        ChatDirectoryTool,
        ChatMessagesTool,
    )

    deps = ChatToolDeps(
        runtime=runtime,
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
    )
    chat_tools: list[BaseTool] = [
        ChatMessagesTool(deps=deps),
        ChatDirectoryTool(deps=deps),
    ]
    logger.debug(
        CHAT_TOOL_GRANTED,
        agent_id=str(identity.id),
        task_id=task_id,
        connection=runtime.connection_name,
        tools=[tool.name for tool in chat_tools],
    )
    existing = list(tool_registry.all_tools())
    return ToolRegistry([*existing, *chat_tools])


def registry_with_deploy_tools(
    tool_registry: ToolRegistry,
    runtime: DeployToolsRuntime | None,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed deploy agent tools when their runtime is wired.

    Returns:
        A :class:`ToolRegistry` with ``deploy_run`` / ``deploy_release``
        appended when both ``runtime`` and ``approval_store`` are wired;
        otherwise the original registry unchanged.
    """
    if runtime is None or approval_store is None:
        _log_if_ungated(
            runtime, approval_store, family="deploy", identity=identity, task_id=task_id
        )
        return tool_registry

    from synthorg.tools.deploy._runtime import DeployToolDeps  # noqa: PLC0415
    from synthorg.tools.deploy.deploy_tools import (  # noqa: PLC0415
        DeployReleaseTool,
        DeployRunTool,
    )

    deps = DeployToolDeps(
        runtime=runtime,
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
    )
    deploy_tools: list[BaseTool] = [
        DeployRunTool(deps=deps),
        DeployReleaseTool(deps=deps, actor=identity),
    ]
    logger.debug(
        DEPLOY_TOOL_GRANTED,
        agent_id=str(identity.id),
        task_id=task_id,
        targets=sorted(runtime.allowed_targets),
        tools=[tool.name for tool in deploy_tools],
    )
    existing = list(tool_registry.all_tools())
    return ToolRegistry([*existing, *deploy_tools])


def registry_with_publish_tools(
    tool_registry: ToolRegistry,
    runtime: PublishToolsRuntime | None,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed publish agent tools when their runtime is wired.

    Returns:
        A :class:`ToolRegistry` with ``publish_inspect`` / ``publish_push``
        appended when both ``runtime`` and ``approval_store`` are wired;
        otherwise the original registry unchanged.
    """
    if runtime is None or approval_store is None:
        _log_if_ungated(
            runtime,
            approval_store,
            family="publish",
            identity=identity,
            task_id=task_id,
        )
        return tool_registry

    from synthorg.tools.publish._runtime import PublishToolDeps  # noqa: PLC0415
    from synthorg.tools.publish.publish_tools import (  # noqa: PLC0415
        PublishInspectTool,
        PublishPushTool,
    )

    deps = PublishToolDeps(
        runtime=runtime,
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
    )
    publish_tools: list[BaseTool] = [
        PublishInspectTool(deps=deps),
        PublishPushTool(deps=deps, actor=identity),
    ]
    logger.debug(
        PUBLISH_TOOL_GRANTED,
        agent_id=str(identity.id),
        task_id=task_id,
        targets=sorted(runtime.allowed_targets),
        tools=[tool.name for tool in publish_tools],
    )
    existing = list(tool_registry.all_tools())
    return ToolRegistry([*existing, *publish_tools])


def registry_with_connection_tools(
    tool_registry: ToolRegistry,
    runtimes: ConnectionToolRuntimes,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add every governed connection-tool family the runtime carries.

    The single owner of the bundle-field-to-builder pairing. A family added
    to :class:`ConnectionToolRuntimes` and resolved at boot but never named
    here is a family whose tools are silently never registered, which type
    checks and boots cleanly, so the pairing lives in one place rather than
    once per family at the call site.

    Returns:
        The registry with each wired family's tools appended.
    """
    registry = registry_with_forge_tools(
        tool_registry,
        runtimes.forge,
        approval_store=approval_store,
        identity=identity,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )
    registry = registry_with_chat_tools(
        registry,
        runtimes.chat,
        approval_store=approval_store,
        identity=identity,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )
    registry = registry_with_deploy_tools(
        registry,
        runtimes.deploy,
        approval_store=approval_store,
        identity=identity,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )
    return registry_with_publish_tools(
        registry,
        runtimes.publish,
        approval_store=approval_store,
        identity=identity,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )


def registry_with_delegate_tool(
    tool_registry: ToolRegistry,
    runner: SubAgentRunner | None,
    *,
    config_resolver: ConfigResolver | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    project_id: str | None = None,
) -> ToolRegistry:
    """Add the blocking ``delegate_and_await`` tool when it can be wired.

    Bound to the supervisor's identity, task, and project so the child
    task records the correct creator / parent / scope. Live-gating on
    ``engine.delegation_enabled`` happens inside the tool per call, so the
    tool is added whenever a runner, resolver, task, and project scope all
    exist; an operator disabling the feature makes it refuse at call time
    rather than vanishing mid-run.

    Returns:
        A :class:`ToolRegistry` with ``delegate_and_await`` appended when
        the runner, resolver, task id, and project scope are all present;
        otherwise the original registry unchanged.
    """
    # Blank task / project ids are treated like a missing id: the tool binds
    # them into a NotBlankStr-typed SubAgentDelegationSpec, so registering it
    # with a whitespace-only scope would only fail later at execute time.
    if (
        runner is None
        or config_resolver is None
        or not (task_id and task_id.strip())
        or not (project_id and project_id.strip())
    ):
        return tool_registry

    from synthorg.tools.communication.delegate_tools import (  # noqa: PLC0415
        DelegateAndAwaitTool,
    )

    delegate_tool = DelegateAndAwaitTool(
        runner=runner,
        config_resolver=config_resolver,
        requested_by=str(identity.id),
        parent_task_id=task_id,
        project=project_id,
    )
    logger.debug(
        DELEGATE_TOOL_GRANTED,
        agent_id=str(identity.id),
        task_id=task_id,
        project_id=project_id,
    )
    existing = list(tool_registry.all_tools())
    return ToolRegistry([*existing, delegate_tool])


__all__ = [
    "registry_with_chat_tools",
    "registry_with_connection_tools",
    "registry_with_delegate_tool",
    "registry_with_deploy_tools",
    "registry_with_forge_tools",
    "registry_with_publish_tools",
]

"""Per-run registry augmentation for the governed forge / chat tools.

Companion to ``_security_factory``'s ``registry_with_*`` family, kept
separate so that module stays within its size budget. Both builders are
run-scoped: they bind the run's identity, task, and effective autonomy
onto the boot-scoped runtime bundle and append the resulting tools to the
agent's registry, gating every write through the identity-bound approval
flow. Each returns the registry unchanged when its runtime bundle or the
approval store is absent (the feature is off, or writes could not be
gated).
"""

from typing import TYPE_CHECKING

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE
from synthorg.observability.events.tool import (
    CHAT_TOOL_GRANTED,
    FORGE_TOOL_GRANTED,
)
from synthorg.security.risk_map import default_risk_classifier
from synthorg.tools.base import BaseTool
from synthorg.tools.chat._runtime import ChatToolsRuntime
from synthorg.tools.forge._runtime import ForgeToolsRuntime
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.core.effective_autonomy import EffectiveAutonomy

logger = get_logger(__name__)


def registry_with_forge_tools(  # noqa: PLR0913 -- run-scoped wiring inputs
    tool_registry: ToolRegistry,
    runtime: ForgeToolsRuntime | None,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed forge agent tools when their runtime is wired.

    Returns:
        A :class:`ToolRegistry` with ``forge_repo`` / ``forge_issue`` /
        ``forge_pull_request`` / ``forge_ci`` appended when both
        ``runtime`` and ``approval_store`` are wired; otherwise the
        original registry unchanged.
    """
    if runtime is None or approval_store is None:
        return tool_registry

    from synthorg.tools.forge._runtime import ForgeToolDeps  # noqa: PLC0415
    from synthorg.tools.forge.forge_tools import (  # noqa: PLC0415
        ForgeCiTool,
        ForgeIssueTool,
        ForgePullRequestTool,
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


def registry_with_chat_tools(  # noqa: PLR0913 -- run-scoped wiring inputs
    tool_registry: ToolRegistry,
    runtime: ChatToolsRuntime | None,
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


__all__ = [
    "registry_with_chat_tools",
    "registry_with_forge_tools",
]

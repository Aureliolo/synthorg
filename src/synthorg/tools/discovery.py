"""Built-in discovery tools for progressive tool disclosure.

Three read-only tools always available to agents:

- ``list_tools`` -- returns L1 metadata for all permitted tools
- ``load_tool`` -- returns L2 body for a specific tool
- ``load_tool_resource`` -- returns a specific L3 resource

Discovery tools signal load/unload state changes via
``ToolExecutionResult.metadata`` keys that the
``DisclosureMiddleware`` observes.
"""

import json
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.core.enums import ToolCategory
from synthorg.core.tool_disclosure import (  # noqa: TC001
    ToolL1Metadata,
    ToolL2Body,
    ToolL3Resource,
)
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_DISCLOSURE_MANAGER_BOUND,
    TOOL_DISCLOSURE_MANAGER_NOT_BOUND,
)
from synthorg.tools._misc_args import (
    ListToolsArgs,
    LoadToolArgs,
    LoadToolResourceArgs,
)

from .base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

# ── Disclosure manager protocol ──────────────────────────────────


@runtime_checkable
class ToolDisclosureManager(Protocol):
    """Protocol for discovery tools to query tool metadata.

    Implemented by ``ToolInvoker`` to break the circular
    dependency between discovery tools and the registry.
    """

    def get_l1_summaries(self) -> tuple[ToolL1Metadata, ...]:
        """Return L1 metadata for all permitted tools.

        Returns:
            Tuple of ``ToolL1Metadata``.
        """
        ...

    def get_l2_body(self, tool_name: str) -> ToolL2Body | None:
        """Return L2 body for a specific tool, or ``None``.

        Returns:
            The matching ``ToolL2Body``, or ``None`` when no match is found.
        """
        ...

    def get_l3_resource(
        self,
        tool_name: str,
        resource_id: str,
    ) -> ToolL3Resource | None:
        """Return a specific L3 resource, or ``None``.

        Returns:
            The matching ``ToolL3Resource``, or ``None`` when no match is found.
        """
        ...


# ── Discovery tools ──────────────────────────────────────────────

_DISCOVERY_NAMES: frozenset[str] = frozenset(
    {"list_tools", "load_tool", "load_tool_resource"},
)
"""Names of the three built-in discovery tools."""

# Metadata keys for discovery tool -> middleware signaling
METADATA_SHOULD_LOAD_TOOL: str = "should_load_tool"
"""Set by ``LoadToolTool`` to signal ``DisclosureMiddleware``."""

METADATA_SHOULD_LOAD_RESOURCE: str = "should_load_resource"
"""Set by ``LoadToolResourceTool`` to signal ``DisclosureMiddleware``."""


class ListToolsTool(BaseTool):
    """Return L1 metadata for all permitted tools.

    Always available regardless of agent access level.
    """

    args_model: ClassVar[type[BaseModel] | None] = ListToolsArgs

    def __init__(self, manager: ToolDisclosureManager) -> None:
        super().__init__(
            name="list_tools",
            description="List all available tools with brief descriptions",
            parameters_schema=ListToolsArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type="memory:read",
        )
        self._manager = manager

    async def execute(
        self,
        *,
        arguments: dict[str, Any],  # noqa: ARG002
    ) -> ToolExecutionResult:
        """Return JSON array of L1 metadata.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        summaries = self._manager.get_l1_summaries()
        payload = [
            {
                "name": s.name,
                "short_description": s.short_description,
                "category": s.category,
                "typical_cost_tier": s.typical_cost_tier,
            }
            for s in summaries
        ]
        return ToolExecutionResult(
            content=json.dumps(payload),
            metadata={"tool_count": len(payload)},
        )


class LoadToolTool(BaseTool):
    """Load a tool's L2 body (full specification).

    Always available regardless of agent access level.
    Sets ``metadata["should_load_tool"]`` to signal the
    ``DisclosureMiddleware`` to mark the tool as loaded.
    """

    args_model: ClassVar[type[BaseModel] | None] = LoadToolArgs

    def __init__(self, manager: ToolDisclosureManager) -> None:
        super().__init__(
            name="load_tool",
            description="Load the full specification for a tool",
            parameters_schema=LoadToolArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type="memory:read",
        )
        self._manager = manager

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Return L2 body JSON for the requested tool.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        tool_name: str = arguments["tool_name"]
        l2 = self._manager.get_l2_body(tool_name)
        if l2 is None:
            return ToolExecutionResult(
                content=f"Tool {tool_name!r} not found or has no L2 body",
                is_error=True,
            )
        payload = {
            "name": tool_name,
            "full_description": l2.full_description,
            "parameter_schema": dict(l2.parameter_schema),
            "usage_examples": list(l2.usage_examples),
            "failure_modes": list(l2.failure_modes),
        }
        return ToolExecutionResult(
            content=json.dumps(payload),
            metadata={METADATA_SHOULD_LOAD_TOOL: tool_name},
        )


class LoadToolResourceTool(BaseTool):
    """Fetch a specific L3 resource for a tool.

    Always available regardless of agent access level.
    Sets ``metadata["should_load_resource"]`` to signal the
    ``DisclosureMiddleware``.
    """

    args_model: ClassVar[type[BaseModel] | None] = LoadToolResourceArgs

    def __init__(self, manager: ToolDisclosureManager) -> None:
        super().__init__(
            name="load_tool_resource",
            description="Load a specific advanced resource for a tool",
            parameters_schema=LoadToolResourceArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
            action_type="memory:read",
        )
        self._manager = manager

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Return L3 resource content.

        Returns:
            Result of type ``ToolExecutionResult``.
        """
        tool_name: str = arguments["tool_name"]
        resource_id: str = arguments["resource_id"]
        resource = self._manager.get_l3_resource(tool_name, resource_id)
        if resource is None:
            return ToolExecutionResult(
                content=(f"Resource {resource_id!r} not found for tool {tool_name!r}"),
                is_error=True,
            )
        payload = {
            "resource_id": resource.resource_id,
            "content_type": resource.content_type,
            "content": resource.content,
            "size_bytes": resource.size_bytes,
        }
        return ToolExecutionResult(
            content=json.dumps(payload),
            metadata={
                METADATA_SHOULD_LOAD_RESOURCE: (tool_name, resource_id),
            },
        )


class DeferredDisclosureManager:
    """Late-binding wrapper for ``ToolDisclosureManager``.

    Allows discovery tools to be created before the
    ``ToolInvoker`` exists.  Call ``bind(invoker)`` after
    invoker construction to activate the manager.

    Raises:
        RuntimeError: If a method is called before ``bind``.
    """

    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate: ToolDisclosureManager | None = None

    def bind(self, delegate: ToolDisclosureManager) -> None:
        """Set the real disclosure manager."""
        self._delegate = delegate
        logger.info(
            TOOL_DISCLOSURE_MANAGER_BOUND,
            delegate_type=type(delegate).__name__,
        )

    def _require_bound(self) -> ToolDisclosureManager:
        """Require bound.

        Returns:
            Result of type ``ToolDisclosureManager``.

        Raises:
            RuntimeError: If the operation fails at runtime.
        """
        if self._delegate is None:
            msg = "DeferredDisclosureManager not yet bound"
            logger.error(
                TOOL_DISCLOSURE_MANAGER_NOT_BOUND,
                note=msg,
            )
            raise RuntimeError(msg)
        return self._delegate

    def get_l1_summaries(self) -> tuple[ToolL1Metadata, ...]:
        """Delegate to bound manager.

        Returns:
            Tuple of ``ToolL1Metadata``.
        """
        return self._require_bound().get_l1_summaries()

    def get_l2_body(self, tool_name: str) -> ToolL2Body | None:
        """Delegate to bound manager.

        Returns:
            The matching ``ToolL2Body``, or ``None`` when no match is found.
        """
        return self._require_bound().get_l2_body(tool_name)

    def get_l3_resource(
        self,
        tool_name: str,
        resource_id: str,
    ) -> ToolL3Resource | None:
        """Delegate to bound manager.

        Returns:
            The matching ``ToolL3Resource``, or ``None`` when no match is found.
        """
        return self._require_bound().get_l3_resource(
            tool_name,
            resource_id,
        )


def build_discovery_tools(
    manager: ToolDisclosureManager | DeferredDisclosureManager,
) -> tuple[BaseTool, ...]:
    """Create the three built-in discovery tools.

    Args:
        manager: Disclosure manager providing L1/L2/L3 queries.
            Can be a ``DeferredDisclosureManager`` that is bound
            after invoker construction.

    Returns:
        Tuple of ``(ListToolsTool, LoadToolTool, LoadToolResourceTool)``.
    """
    return (
        ListToolsTool(manager),
        LoadToolTool(manager),
        LoadToolResourceTool(manager),
    )

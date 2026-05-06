"""Disclosure middleware for progressive tool loading.

Observes ``load_tool`` and ``load_tool_resource`` tool calls
and updates ``AgentContext`` with the loaded state.  Triggers
auto-unload when context budget pressure exceeds the configured
threshold.
"""

import json

from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    TOOL_AUTO_UNLOADED,
    TOOL_DISCLOSURE_LOAD_FAILED,
    TOOL_L2_LOADED,
    TOOL_L3_FETCHED,
)
from synthorg.tools.disclosure_config import ToolDisclosureConfig
from synthorg.tools.discovery import (
    METADATA_SHOULD_LOAD_RESOURCE,
    METADATA_SHOULD_LOAD_TOOL,
)

from .models import AgentMiddlewareContext, ToolCallResult  # noqa: TC001
from .protocol import BaseAgentMiddleware, ToolCallable

logger = get_logger(__name__)


class DisclosureMiddleware(BaseAgentMiddleware):
    """Middleware for progressive tool disclosure state management.

    Observes successful ``load_tool`` and ``load_tool_resource``
    calls and updates ``AgentContext.loaded_tools`` and
    ``loaded_resources`` via ``model_copy``.  Triggers FIFO
    auto-unload when ``auto_unload_on_budget_pressure`` is enabled
    and context fill exceeds ``unload_threshold_percent``.
    """

    __slots__ = ("_config",)

    def __init__(
        self,
        *,
        config: ToolDisclosureConfig | None = None,
    ) -> None:
        super().__init__(name="disclosure")
        self._config = config or ToolDisclosureConfig()

    async def wrap_tool_call(  # noqa: C901, PLR0912
        self,
        ctx: AgentMiddlewareContext,
        call: ToolCallable,
    ) -> ToolCallResult:
        """Observe discovery tool calls and manage loaded state."""
        result = await call(ctx)

        if not result.success:
            return result

        agent_ctx = ctx.agent_context
        original_agent_ctx = agent_ctx
        just_loaded: str | None = None

        if result.tool_name == "load_tool":
            tool_name = result.metadata.get(METADATA_SHOULD_LOAD_TOOL)
            if not tool_name:
                tool_name = self._extract_tool_name(result.output)
            if not tool_name:
                logger.warning(
                    TOOL_DISCLOSURE_LOAD_FAILED,
                    execution_id=agent_ctx.execution_id,
                    note="could not extract tool name from metadata or output",
                    turn=agent_ctx.turn_count,
                )
            if tool_name and tool_name not in agent_ctx.loaded_tools:
                agent_ctx = agent_ctx.with_tool_loaded(tool_name)
                just_loaded = tool_name
                logger.info(
                    TOOL_L2_LOADED,
                    execution_id=agent_ctx.execution_id,
                    tool_name=tool_name,
                    turn=agent_ctx.turn_count,
                )
                ctx = ctx.model_copy(update={"agent_context": agent_ctx})

        elif result.tool_name == "load_tool_resource":
            pair = result.metadata.get(METADATA_SHOULD_LOAD_RESOURCE)
            if (
                isinstance(pair, (tuple, list))
                and len(pair) == 2  # noqa: PLR2004
                and isinstance(pair[0], str)
                and isinstance(pair[1], str)
            ):
                tool_name, resource_id = pair[0], pair[1]
                if (tool_name, resource_id) not in agent_ctx.loaded_resources:
                    agent_ctx = agent_ctx.with_resource_loaded(
                        tool_name,
                        resource_id,
                    )
                    logger.info(
                        TOOL_L3_FETCHED,
                        execution_id=agent_ctx.execution_id,
                        tool_name=tool_name,
                        resource_id=resource_id,
                        turn=agent_ctx.turn_count,
                    )
                    ctx = ctx.model_copy(update={"agent_context": agent_ctx})

        # Auto-unload under budget pressure (skip tool just loaded)
        agent_ctx = ctx.agent_context
        if (
            self._config.auto_unload_on_budget_pressure
            and agent_ctx.context_fill_percent is not None
            and agent_ctx.context_fill_percent >= self._config.unload_threshold_percent
            and agent_ctx.tool_load_order
        ):
            oldest = agent_ctx.tool_load_order[0]
            if oldest == just_loaded and len(agent_ctx.tool_load_order) > 1:
                oldest = agent_ctx.tool_load_order[1]
            elif oldest == just_loaded:
                oldest = None  # type: ignore[assignment]
            if oldest:
                agent_ctx = agent_ctx.with_tool_unloaded(oldest)
                logger.info(
                    TOOL_AUTO_UNLOADED,
                    execution_id=agent_ctx.execution_id,
                    tool_name=oldest,
                    context_fill_percent=agent_ctx.context_fill_percent,
                    turn=agent_ctx.turn_count,
                )
                ctx = ctx.model_copy(update={"agent_context": agent_ctx})

        # Propagate updated context via result so callers can apply it
        if ctx.agent_context is not original_agent_ctx:
            result = result.model_copy(
                update={"updated_agent_context": ctx.agent_context},
            )

        return result

    @staticmethod
    def _extract_tool_name(output: str) -> str | None:
        """Extract loaded tool name from load_tool JSON output."""
        try:
            data = json.loads(output)
        except json.JSONDecodeError, TypeError, AttributeError:
            logger.debug(
                TOOL_DISCLOSURE_LOAD_FAILED,
                note="failed to parse load_tool output as JSON",
                output_preview=output[:200],
            )
            return None
        name = data.get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            return name
        logger.debug(
            TOOL_DISCLOSURE_LOAD_FAILED,
            note="JSON output missing valid 'name' key",
            output_preview=output[:200],
        )
        return None

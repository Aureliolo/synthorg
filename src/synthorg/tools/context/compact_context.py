"""Agent-controlled context compaction tool.

Allows agents to explicitly request context compaction when context
fill is high and reasoning clarity is critical.  The tool signals
intent via metadata -- it does NOT mutate the frozen AgentContext
directly.  The execution loop detects the directive and invokes
compaction at the turn boundary.
"""

from typing import ClassVar, Literal, cast, override

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.sanitization import sanitize_message
from synthorg.observability import get_logger
from synthorg.observability.events.context_budget import (
    CONTEXT_BUDGET_AGENT_COMPACTION_REQUESTED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

CompactContextStrategy = Literal["summarize"]


class CompactContextArgs(BaseModel):
    """Args for ``compact_context``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: CompactContextStrategy = Field(description="Compaction strategy")
    reason: NotBlankStr = Field(
        min_length=10,
        max_length=256,
        description="Brief explanation for why compaction is needed",
    )
    preserve_markers: bool = Field(
        default=True,
        description="Preserve epistemic markers in the compaction summary",
    )


class CompactContextTool(BaseTool):
    """Signal context compaction to the execution loop.

    The tool validates arguments and returns a compaction directive
    in ``ToolExecutionResult.metadata``.  The execution loop detects
    the directive and performs actual compaction at the turn boundary.

    This tool is stateless and safe to register unconditionally.
    Compaction only triggers when ``CompactionConfig.agent_controlled``
    is enabled in the engine configuration.
    """

    args_model: ClassVar[type[BaseModel] | None] = CompactContextArgs

    def __init__(self) -> None:
        super().__init__(
            name="compact_context",
            description=(
                "Request context compaction when conversation has "
                "grown large. Preserves recent turns and creates a "
                "summary of older exchanges. Use when context fill "
                "is high and accuracy on complex reasoning is "
                "critical."
            ),
            parameters_schema=CompactContextArgs.model_json_schema(),
            category=ToolCategory.MEMORY,
        )

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Signal compaction directive via metadata.

        Args:
            arguments: Validated tool arguments (strategy, reason,
                optionally preserve_markers).

        Returns:
            Result with ``compaction_directive`` metadata key.
        """
        strategy = cast("str", arguments.get("strategy", "summarize"))
        reason = cast("str", arguments.get("reason", ""))
        preserve_markers = cast("bool", arguments.get("preserve_markers", True))
        sanitized_reason = sanitize_message(reason, max_length=256)

        logger.info(
            CONTEXT_BUDGET_AGENT_COMPACTION_REQUESTED,
            strategy=strategy,
            preserve_markers=preserve_markers,
            reason=sanitized_reason,
        )

        return ToolExecutionResult(
            content=("Compaction directive accepted. Will execute at turn boundary."),
            metadata={
                "compaction_directive": True,
                "strategy": strategy,
                "preserve_markers": preserve_markers,
                "reason": sanitized_reason,
            },
        )

"""Agent-controlled context compaction tool.

Allows agents to explicitly request context compaction when context
fill is high and reasoning clarity is critical.  The tool does NOT mutate
the frozen ``AgentContext`` directly: the loop observes this tool's CALL at
the existing turn-boundary side-effect seam
(``loop_tool_execution.py::_apply_tool_call_side_effect``) and forces
compaction there, skipping the fill-threshold check and honouring the
requested ``preserve_markers`` override.
"""

from typing import ClassVar, Literal, override

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr
from synthorg.engine.sanitization import sanitize_message
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

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

    The tool validates arguments; the loop reads the tool CALL itself
    (this class's own ``strategy`` / ``reason`` / ``preserve_markers``
    arguments) at the turn-boundary side-effect seam and forces compaction
    there. ``ToolExecutionResult.metadata`` is set for the same information
    but is never read back by the loop -- it is dropped at the invoker
    boundary before the loop ever sees it.

    This tool is stateless and safe to register unconditionally.
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
        """Record the compaction directive; the loop reads the call, not this result.

        The loop observes this tool's CALL (its own arguments), not
        ``ToolExecutionResult.metadata``, which is dropped at the invoker
        boundary and never reaches it -- so the directive this records here
        is for the operator reading the transcript, not a signal anything
        downstream consumes.

        Args:
            arguments: Validated tool arguments (strategy, reason,
                optionally preserve_markers).

        Returns:
            Result reporting the directive; ``compaction_directive`` metadata
            kept for the same reason, not consumed by the loop.
        """
        args = parse_typed("tool.execute", arguments, CompactContextArgs)
        strategy = args.strategy
        reason = args.reason
        preserve_markers = args.preserve_markers
        sanitized_reason = sanitize_message(reason, max_length=256)

        return ToolExecutionResult(
            content=(
                f"Compaction requested (strategy={strategy!r}). It will run "
                "before your next turn."
            ),
            metadata={
                "compaction_directive": True,
                "strategy": strategy,
                "preserve_markers": preserve_markers,
                "reason": sanitized_reason,
            },
        )

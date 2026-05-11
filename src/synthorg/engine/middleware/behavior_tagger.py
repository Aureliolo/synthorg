"""Behavior tagger middleware.

Infers ``BehaviorTag`` categories from model responses by
matching tool names against a configurable mapping.  Operates
in the ``after_model`` slot (post-LLM, pre-tool-execution).

Opt-in: registered in the middleware registry but NOT in the
default agent chain.  Enable by adding ``"behavior_tagger"``
to the company's ``AgentMiddlewareConfig.chain``.
"""

from typing import Final

from synthorg.engine.loop_protocol import BehaviorTag
from synthorg.engine.middleware.models import AgentMiddlewareContext  # noqa: TC001
from synthorg.engine.middleware.protocol import BaseAgentMiddleware
from synthorg.observability import get_logger
from synthorg.observability.events.behavior_tagging import (
    BEHAVIOR_TAG_INFERRED,
    BEHAVIOR_TAGGER_ERROR,
    BEHAVIOR_TAGGER_SKIP,
)

logger = get_logger(__name__)


# ── Default tool name -> BehaviorTag mapping ───────────────────────

_DEFAULT_TOOL_TAG_MAP: dict[str, BehaviorTag] = {
    # File operations
    "read_file": BehaviorTag.FILE_OPERATIONS,
    "write_file": BehaviorTag.FILE_OPERATIONS,
    "edit_file": BehaviorTag.FILE_OPERATIONS,
    "list_files": BehaviorTag.FILE_OPERATIONS,
    "list_directory": BehaviorTag.FILE_OPERATIONS,
    "grep": BehaviorTag.FILE_OPERATIONS,
    "glob": BehaviorTag.FILE_OPERATIONS,
    # Retrieval
    "search": BehaviorTag.RETRIEVAL,
    "web_search": BehaviorTag.RETRIEVAL,
    "web_fetch": BehaviorTag.RETRIEVAL,
    "fetch_url": BehaviorTag.RETRIEVAL,
    # Memory
    "memory_read": BehaviorTag.MEMORY,
    "memory_write": BehaviorTag.MEMORY,
    "memory_search": BehaviorTag.MEMORY,
    "memory_store": BehaviorTag.MEMORY,
    # Delegation
    "delegate": BehaviorTag.DELEGATION,
    "delegate_task": BehaviorTag.DELEGATION,
    "spawn_agent": BehaviorTag.DELEGATION,
    # Verification
    "verify": BehaviorTag.VERIFICATION,
    "grade": BehaviorTag.VERIFICATION,
    "evaluate": BehaviorTag.VERIFICATION,
    # Coordination
    "send_message": BehaviorTag.COORDINATION,
    "broadcast": BehaviorTag.COORDINATION,
}

# Output token threshold for inferring SUMMARIZATION vs CONVERSATION.
_SUMMARIZATION_TOKEN_THRESHOLD: Final[int] = 500


class BehaviorTaggerMiddleware(BaseAgentMiddleware):
    """Infers behavior tags from model responses.

    Matches tool names (from pending tool calls in the model
    response) against a configurable prefix map.  Falls back to
    ``CONVERSATION`` (text-only, short) or ``SUMMARIZATION``
    (text-only, long) when no tools are present.

    Args:
        tool_tag_map: Custom tool-name to tag mapping.  Falls back
            to ``_DEFAULT_TOOL_TAG_MAP`` when ``None``.
    """

    def __init__(
        self,
        *,
        tool_tag_map: dict[str, BehaviorTag] | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="behavior_tagger")
        self._tool_tag_map = tool_tag_map or dict(_DEFAULT_TOOL_TAG_MAP)

    async def after_model(
        self,
        ctx: AgentMiddlewareContext,
    ) -> AgentMiddlewareContext:
        """Infer behavior tags and attach to context metadata.

        Args:
            ctx: Current middleware context.

        Returns:
            Context with ``behavior_tags`` in metadata.
        """
        try:
            tags = self._infer_tags(ctx)
        except Exception:
            logger.exception(
                BEHAVIOR_TAGGER_ERROR,
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
            )
            return ctx

        if not tags:
            logger.debug(
                BEHAVIOR_TAGGER_SKIP,
                agent_id=ctx.agent_id,
                task_id=ctx.task_id,
            )
            return ctx

        logger.debug(
            BEHAVIOR_TAG_INFERRED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            tags=[t.value for t in tags],
        )
        return ctx.with_metadata("behavior_tags", tuple(tags))

    def _infer_tags(
        self,
        ctx: AgentMiddlewareContext,
    ) -> tuple[BehaviorTag, ...]:
        """Infer tags from tool calls or text content.

        Returns:
            Deduplicated tuple of inferred tags.
        """
        tags: set[BehaviorTag] = set()

        # Check pending tool calls from the model response.
        pending_tools: tuple[str, ...] = ctx.metadata.get(
            "pending_tool_calls",
            (),
        )
        for tool_name in pending_tools:
            tag = self._match_tool(tool_name)
            if tag is not None:
                tags.add(tag)

        # Also check tool_calls_made on the context if available.
        tool_calls_made: tuple[str, ...] = ctx.metadata.get(
            "tool_calls_made",
            (),
        )
        for tool_name in tool_calls_made:
            tag = self._match_tool(tool_name)
            if tag is not None:
                tags.add(tag)

        has_tools = bool(pending_tools or tool_calls_made)

        if not tags and has_tools:
            # Tools were called but none matched a specific category.
            tags.add(BehaviorTag.TOOL_USE)
        elif not tags:
            # No tools at all -- infer from text content.
            output_tokens = ctx.metadata.get("output_tokens", 0)
            is_long = (
                isinstance(output_tokens, int)
                and output_tokens >= _SUMMARIZATION_TOKEN_THRESHOLD
            )
            if is_long:
                tags.add(BehaviorTag.SUMMARIZATION)
            else:
                tags.add(BehaviorTag.CONVERSATION)

        return tuple(sorted(tags, key=lambda t: t.value))

    def _match_tool(self, tool_name: str) -> BehaviorTag | None:
        """Match a tool name against the tag map.

        Tries exact match first, then prefix match (tool names
        may include namespaced prefixes like ``mcp__server__tool``).
        """
        # Exact match.
        if tool_name in self._tool_tag_map:
            return self._tool_tag_map[tool_name]

        # Prefix match: strip namespace prefixes (e.g. mcp__server__tool).
        if "__" in tool_name:
            base = tool_name.rsplit("__", maxsplit=1)[-1]
            if base in self._tool_tag_map:
                return self._tool_tag_map[base]

        return None

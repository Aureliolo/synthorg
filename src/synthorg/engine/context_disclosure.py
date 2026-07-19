# module-kind: code
"""Progressive tool-disclosure consistency rule for the agent context.

The disclosure state on :class:`~synthorg.engine.context.AgentContext` is a
pair: a ``loaded_tools`` set and an insertion-ordered ``tool_load_order``
tuple used for FIFO auto-unload. The two must stay in lock-step. The check
lives here as a pure function so it is testable on its own and the context
module stays within its size budget.
"""


def validate_tool_disclosure(
    loaded_tools: frozenset[str],
    tool_load_order: tuple[str, ...],
) -> None:
    """Assert the tool-disclosure set and load order agree.

    Args:
        loaded_tools: Tool names with an L2 body active in context.
        tool_load_order: Insertion-ordered tool names for FIFO unload.

    Raises:
        ValueError: When ``tool_load_order`` names a different set than
            ``loaded_tools``, or carries duplicate names.
    """
    order_set = set(tool_load_order)
    if order_set != loaded_tools:
        msg = (
            f"loaded_tools={loaded_tools} and "
            f"tool_load_order={tool_load_order} are inconsistent"
        )
        raise ValueError(msg)
    if len(tool_load_order) != len(order_set):
        msg = f"tool_load_order contains duplicates: {tool_load_order}"
        raise ValueError(msg)


__all__ = ["validate_tool_disclosure"]

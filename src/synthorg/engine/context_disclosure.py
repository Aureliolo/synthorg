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


def tool_loaded_update(
    loaded_tools: frozenset[str],
    tool_load_order: tuple[str, ...],
    tool_name: str,
) -> dict[str, object] | None:
    """Compute the disclosure-state update for loading a tool's L2 body.

    Args:
        loaded_tools: Tool names with an L2 body active in context.
        tool_load_order: Insertion-ordered tool names for FIFO unload.
        tool_name: Name of the tool to load.

    Returns:
        The context field update, or ``None`` when the tool is already loaded
        (the caller then returns the context unchanged).
    """
    if tool_name in loaded_tools:
        return None
    return {
        "loaded_tools": loaded_tools | {tool_name},
        "tool_load_order": (*tool_load_order, tool_name),
    }


def tool_unloaded_update(
    loaded_tools: frozenset[str],
    tool_load_order: tuple[str, ...],
    loaded_resources: frozenset[tuple[str, str]],
    tool_name: str,
) -> dict[str, object] | None:
    """Compute the disclosure-state update for unloading a tool.

    Also drops any L3 resources belonging to the unloaded tool, so a tool's
    resources never outlive the tool body that disclosed them.

    Args:
        loaded_tools: Tool names with an L2 body active in context.
        tool_load_order: Insertion-ordered tool names for FIFO unload.
        loaded_resources: ``(tool_name, resource_id)`` pairs with L3 active.
        tool_name: Name of the tool to unload.

    Returns:
        The context field update, or ``None`` when the tool is not loaded.
    """
    if tool_name not in loaded_tools:
        return None
    return {
        "loaded_tools": loaded_tools - {tool_name},
        "tool_load_order": tuple(t for t in tool_load_order if t != tool_name),
        "loaded_resources": frozenset(
            (t, r) for t, r in loaded_resources if t != tool_name
        ),
    }


def resource_loaded_update(
    loaded_resources: frozenset[tuple[str, str]],
    tool_name: str,
    resource_id: str,
) -> dict[str, object] | None:
    """Compute the state update for fetching an L3 resource.

    Args:
        loaded_resources: ``(tool_name, resource_id)`` pairs with L3 active.
        tool_name: Name of the tool owning the resource.
        resource_id: Identifier of the resource.

    Returns:
        The context field update, or ``None`` when already loaded.
    """
    pair = (tool_name, resource_id)
    if pair in loaded_resources:
        return None
    return {"loaded_resources": loaded_resources | {pair}}


__all__ = [
    "resource_loaded_update",
    "tool_loaded_update",
    "tool_unloaded_update",
    "validate_tool_disclosure",
]

"""Shared output-style enforcement for the inter-agent message boundary.

The MCP ``MessageService`` publishes agent-authored text on the ``MESSAGE``
channel: this guard enforces the hard policy on every text part, applies an
auto-rewrite back onto the part, and on a hard violation logs the sender context
(which the interceptor cannot see) before re-raising. Deferred import of the
engine subsystem breaks the engine/communication cold-import cycle.
"""

from synthorg.communication.message import Message, Part, TextPart
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.output_style import OUTPUT_STYLE_GATE_REJECTED

logger = get_logger(__name__)


def guard_message_output(message: Message, *, agent_id: str) -> Message:
    """Enforce the output-style policy on a message's text parts before publish.

    Args:
        message: The constructed message about to be published.
        agent_id: The sending agent, logged for traceability on a rejection.

    Returns:
        The message, with any auto-rewritten text parts substituted (the same
        object when nothing changed).

    Raises:
        OutputPolicyViolationError: When a non-exempt hard rule blocks; logged
            with sender context first, then re-raised for the agent to rework.
    """
    from synthorg.engine.output_style import (  # noqa: PLC0415
        OutputChannel,
        OutputContext,
        OutputPolicyViolationError,
        enforce_output_policy,
    )

    ctx = OutputContext(channel=OutputChannel.MESSAGE)
    changed = False
    new_parts: list[Part] = []
    try:
        for part in message.parts:
            if isinstance(part, TextPart):
                guarded = enforce_output_policy(part.text, ctx)
                if guarded != part.text:
                    new_parts.append(part.model_copy(update={"text": guarded}))
                    changed = True
                    continue
            new_parts.append(part)
    except OutputPolicyViolationError as exc:
        logger.warning(
            OUTPUT_STYLE_GATE_REJECTED,
            channel=OutputChannel.MESSAGE.value,
            agent_id=agent_id,
            to=message.to,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    if not changed:
        return message
    return message.model_copy(update={"parts": tuple(new_parts)})


__all__ = ["guard_message_output"]

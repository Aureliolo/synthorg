"""Vendor-neutral inbound chat event + its kind.

The Socket-Mode decoder maps each platform frame onto :class:`InboundChatEvent`
so the consumer and router stay platform-agnostic (the same way the outbound
:mod:`synthorg.integrations.chat_api.protocol` models decouple the send tools
from Slack). ``text`` is raw, attacker-controlled human input and MUST be
fenced with ``wrap_untrusted(TAG_TASK_DATA, ...)`` before it reaches any LLM
prompt; nothing in this package feeds it to a prompt unfenced.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from synthorg.core.types import NotBlankStr


class InboundEventKind(StrEnum):
    """The inbound chat event kinds that can resume a task."""

    MENTION = "mention"
    MESSAGE = "message"
    DIRECT_MESSAGE = "direct_message"
    REACTION = "reaction"


class InboundChatEvent(BaseModel):
    """A single decoded inbound chat event on the bound connection.

    ``thread_ts`` is the correlation key back to the task that posted the
    thread root (empty for a top-level message). ``reaction`` is populated
    only for :attr:`InboundEventKind.REACTION`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: InboundEventKind
    # NotBlankStr, not str: a whitespace-only channel or user is truthy, so a
    # plain emptiness check would admit one that can be neither correlated
    # back to its thread nor attributed to a decider.
    channel: NotBlankStr
    user: NotBlankStr
    text: str = ""
    ts: str = ""
    thread_ts: str = ""
    reaction: str = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        """Enforce the cross-field invariant a routable event must hold.

        The ``reaction`` field is meaningful only for the REACTION kind, so
        the agreement between them is a type-level guarantee rather than
        decoder-only discipline.

        Returns:
            The unchanged event.

        Raises:
            ValueError: When the reaction field and the REACTION kind
                disagree.
        """
        if (self.kind is InboundEventKind.REACTION) != bool(self.reaction):
            msg = "reaction must be set iff the event kind is REACTION"
            raise ValueError(msg)
        return self


__all__ = ["InboundChatEvent", "InboundEventKind"]

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
    channel: str
    user: str
    text: str = ""
    ts: str = ""
    thread_ts: str = ""
    reaction: str = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> Self:
        """Enforce the cross-field invariants a routable event must hold.

        A blank channel/user cannot be correlated or attributed, and the
        ``reaction`` field is meaningful only for the REACTION kind, so both
        become type-level guarantees rather than decoder-only discipline.

        Returns:
            The unchanged event.

        Raises:
            ValueError: When channel/user is blank, or the reaction field
                and the REACTION kind disagree.
        """
        if not self.channel or not self.user:
            msg = "an inbound chat event requires a non-blank channel and user"
            raise ValueError(msg)
        if (self.kind is InboundEventKind.REACTION) != bool(self.reaction):
            msg = "reaction must be set iff the event kind is REACTION"
            raise ValueError(msg)
        return self


__all__ = ["InboundChatEvent", "InboundEventKind"]

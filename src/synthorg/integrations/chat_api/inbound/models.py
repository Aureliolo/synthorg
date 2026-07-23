"""Vendor-neutral inbound chat event + its kind.

The Socket-Mode decoder maps each platform frame onto :class:`InboundChatEvent`
so the consumer and router stay platform-agnostic (the same way the outbound
:mod:`synthorg.integrations.chat_api.protocol` models decouple the send tools
from Slack). ``text`` is raw, attacker-controlled human input and MUST be
fenced with ``wrap_untrusted(TAG_TASK_DATA, ...)`` before it reaches any LLM
prompt (SEC-1); nothing in this package feeds it to a prompt unfenced.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


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


__all__ = ["InboundChatEvent", "InboundEventKind"]

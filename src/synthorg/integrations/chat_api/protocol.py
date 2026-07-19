"""Chat-platform Web API client protocol + vendor-neutral models.

The agent-facing chat tools drive this two-way surface (send / read /
list channels / look up user). Concrete clients live beside it
(``slack``) and are selected by :class:`ConnectionType` via
``build_chat_api_client``. The models are SynthOrg's own contract
(frozen, ``extra="forbid"``); each client maps its native payload onto
them. Slack is the first provider; others slot in by registering a
client, so the tool surface stays vendor-neutral.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr


class _ChatResult(BaseModel):
    """Base config for the vendor-neutral chat result models."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class ChatMessageRef(_ChatResult):
    """A reference to a posted message (channel + timestamp id)."""

    channel: str
    ts: str


class ChatMessage(_ChatResult):
    """A single chat message."""

    ts: str
    author: str
    text: str
    thread_ts: str = ""


class ChatChannel(_ChatResult):
    """A chat channel/conversation."""

    id: NotBlankStr
    name: str
    is_private: bool = False
    is_member: bool = False


class ChatUser(_ChatResult):
    """A chat workspace user."""

    id: NotBlankStr
    name: str
    real_name: str = ""
    email: str = ""
    is_bot: bool = False


@runtime_checkable
class ChatApiClient(Protocol):
    """Two-way chat Web API surface for the agent-facing chat tools.

    Every method raises the typed chat errors (``ChatApiAuthError`` on a
    rejected token, ``ChatApiRateLimitError`` on rate limit,
    ``ChatApiError`` on any other failure). Tokens travel in the
    ``Authorization`` header only and are never logged.
    """

    async def send_message(
        self, *, channel: NotBlankStr, text: NotBlankStr, thread_ts: str | None = None
    ) -> ChatMessageRef:
        """Post ``text`` to ``channel`` (optionally in a thread)."""
        ...

    async def read_channel(
        self, *, channel: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        """Read the most recent messages in ``channel``."""
        ...

    async def read_thread(
        self, *, channel: NotBlankStr, thread_ts: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        """Read the replies in a thread."""
        ...

    async def list_channels(self, *, limit: int) -> tuple[ChatChannel, ...]:
        """List the channels the bot can see."""
        ...

    async def lookup_user(
        self, *, user_id: str | None = None, email: str | None = None
    ) -> ChatUser:
        """Look up a user by id or email (exactly one must be given)."""
        ...

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        ...


__all__ = [
    "ChatApiClient",
    "ChatChannel",
    "ChatMessage",
    "ChatMessageRef",
    "ChatUser",
]

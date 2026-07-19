"""Frozen argument models for the resource-grouped chat tools.

Each tool dispatches on an ``action`` field. Only ``send`` mutates state
(and so routes through the approval gate); reads and directory lookups do
not.
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

_DEFAULT_LIST_LIMIT: Final[int] = 30
_MAX_LIST_LIMIT: Final[int] = 200


class _ChatArgsBase(BaseModel):
    """Shared config for the chat tool args."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class ChatMessagesArgs(_ChatArgsBase):
    """Arguments for the ``chat_messages`` tool."""

    action: Literal["send", "read_channel", "read_thread"]
    channel: NotBlankStr
    text: str = ""
    thread_ts: str = ""
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)

    @property
    def is_write(self) -> bool:
        """Only sending a message mutates chat state."""
        return self.action == "send"

    @model_validator(mode="after")
    def _validate_action(self) -> ChatMessagesArgs:
        if self.action == "send" and not self.text.strip():
            msg = "send requires a non-blank 'text'"
            raise ValueError(msg)
        if self.action == "read_thread" and not self.thread_ts:
            msg = "read_thread requires a 'thread_ts'"
            raise ValueError(msg)
        return self


class ChatDirectoryArgs(_ChatArgsBase):
    """Arguments for the ``chat_directory`` tool (read-only)."""

    action: Literal["list_channels", "lookup_user"]
    user_id: str = ""
    email: str = ""
    limit: int = Field(default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT)

    @property
    def is_write(self) -> bool:
        """Directory lookups never mutate chat state."""
        return False

    @model_validator(mode="after")
    def _validate_action(self) -> ChatDirectoryArgs:
        if self.action == "lookup_user" and bool(self.user_id) == bool(self.email):
            msg = "lookup_user requires exactly one of 'user_id' or 'email'"
            raise ValueError(msg)
        return self


__all__ = ["ChatDirectoryArgs", "ChatMessagesArgs"]

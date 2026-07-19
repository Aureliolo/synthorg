"""Slack Web API chat client (send / read / channels / users).

Drives the bot-token Slack Web API on top of :class:`BaseChatClient`.
Slack returns HTTP 200 even for API-level failures, carrying
``{"ok": false, "error": "..."}``; :func:`_require_ok` turns that
envelope into the typed chat errors so an ``ok=false`` never reaches the
caller as a success. Payload parsing + mapping onto the vendor-neutral
:mod:`protocol` models is local to this module.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.integrations.chat_api._base import BaseChatClient
from synthorg.integrations.chat_api._http import raise_for_chat_status
from synthorg.integrations.chat_api.protocol import (
    ChatChannel,
    ChatMessage,
    ChatMessageRef,
    ChatUser,
)
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
    ChatApiRateLimitError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CHAT_API_ENVELOPE_FAILED,
    CHAT_API_MESSAGE_SENT,
)

logger = get_logger(__name__)

# Slack ``ok=false`` error codes that mean the token is bad or lacks a
# required scope: surfaced as an auth error, not a generic failure.
_AUTH_ERRORS: Final[frozenset[str]] = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "token_revoked",
        "no_permission",
        "missing_scope",
        "ekm_access_denied",
    }
)
_RATE_LIMITED_ERROR: Final[str] = "ratelimited"
_CHANNEL_TYPES: Final[str] = "public_channel,private_channel"


class _SlBase(BaseModel):  # lint-allow: frozen-extra-forbid -- chat extras
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")


class _SlProfile(_SlBase):
    email: str = ""


class _SlUser(_SlBase):
    id: str = ""
    name: str = ""
    real_name: str = ""
    is_bot: bool = False
    profile: _SlProfile | None = None


class _SlChannel(_SlBase):
    id: str = ""
    name: str = ""
    is_private: bool = False
    is_member: bool = False


class _SlMessage(_SlBase):
    ts: str = ""
    user: str = ""
    text: str = ""
    thread_ts: str = ""


class SlackChatClient(BaseChatClient):
    """Two-way Slack Web API client for the agent-facing chat tools."""

    def __init__(self, *, api_base_url: str, token: str, timeout: float) -> None:
        super().__init__(
            api_base_url=api_base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    async def send_message(
        self, *, channel: NotBlankStr, text: NotBlankStr, thread_ts: str | None = None
    ) -> ChatMessageRef:
        """Post ``text`` to ``channel`` (optionally in a thread).

        Returns:
            A reference to the posted message.
        """
        payload: dict[str, object] = {"channel": str(channel), "text": str(text)}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = await self._call(
            "POST", "chat.postMessage", action="send message", json=payload
        )
        ref = ChatMessageRef(
            channel=_str(data.get("channel")) or str(channel),
            ts=_str(data.get("ts")),
        )
        logger.info(CHAT_API_MESSAGE_SENT, channel=ref.channel)
        return ref

    async def read_channel(
        self, *, channel: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        """Read the most recent messages in ``channel``.

        Returns:
            The messages, newest first (as Slack returns them).
        """
        data = await self._call(
            "GET",
            "conversations.history",
            action="read channel",
            params={"channel": str(channel), "limit": limit},
        )
        return _messages_from(data)

    async def read_thread(
        self, *, channel: NotBlankStr, thread_ts: NotBlankStr, limit: int
    ) -> tuple[ChatMessage, ...]:
        """Read the replies in a thread.

        Returns:
            The thread messages, oldest first (as Slack returns them).
        """
        data = await self._call(
            "GET",
            "conversations.replies",
            action="read thread",
            params={"channel": str(channel), "ts": str(thread_ts), "limit": limit},
        )
        return _messages_from(data)

    async def list_channels(self, *, limit: int) -> tuple[ChatChannel, ...]:
        """List the channels the bot can see.

        Returns:
            The visible channels.
        """
        data = await self._call(
            "GET",
            "conversations.list",
            action="list channels",
            params={"limit": limit, "types": _CHANNEL_TYPES},
        )
        raw = data.get("channels")
        entries = raw if isinstance(raw, list) else []
        return tuple(
            _channel_from(_parse(item, _SlChannel, what="channel")) for item in entries
        )

    async def lookup_user(
        self, *, user_id: str | None = None, email: str | None = None
    ) -> ChatUser:
        """Look up a user by id or email.

        Returns:
            The user.

        Raises:
            ChatApiError: When neither ``user_id`` nor ``email`` is given.
        """
        if user_id:
            data = await self._call(
                "GET", "users.info", action="look up user", params={"user": user_id}
            )
        elif email:
            data = await self._call(
                "GET",
                "users.lookupByEmail",
                action="look up user",
                params={"email": email},
            )
        else:
            msg = "lookup_user requires a user_id or an email"
            raise ChatApiError(msg)
        return _user_from(_parse(data.get("user"), _SlUser, what="user"))

    async def _call(
        self,
        method: str,
        api_method: str,
        *,
        action: str,
        json: dict[str, object] | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> Mapping[str, object]:
        """Issue a Slack call and return its ``ok=true`` payload.

        Returns:
            The response body once the Slack ``ok`` envelope is verified.

        Raises:
            ChatApiAuthError / ChatApiRateLimitError / ChatApiError: Per
                the HTTP status or the Slack ``ok=false`` error code.
        """
        resp = await self._request(
            method, api_method, action=action, json=json, params=params
        )
        raise_for_chat_status(resp, action=action)
        return _require_ok(resp.json(), action=action)


def _require_ok(data: object, *, action: str) -> Mapping[str, object]:
    """Return ``data`` when Slack reports ``ok=true`` or raise a typed error.

    Returns:
        The response mapping.

    Raises:
        ChatApiAuthError: When the Slack error code is an auth/scope failure.
        ChatApiRateLimitError: When Slack reports ``ratelimited``.
        ChatApiError: On a malformed response or any other ``ok=false``.
    """
    if not isinstance(data, Mapping):
        logger.warning(CHAT_API_ENVELOPE_FAILED, action=action, reason="malformed")
        msg = f"malformed Slack response while attempting to {action}"
        raise ChatApiError(msg)
    if data.get("ok") is True:
        return data
    # The Slack ``error`` code is a small enum-like token (never the raw
    # body), safe to log for operator triage of the primary failure mode.
    error = _str(data.get("error")) or "unknown"
    logger.warning(CHAT_API_ENVELOPE_FAILED, action=action, slack_error=error)
    if error in _AUTH_ERRORS:
        msg = f"Slack rejected the token while attempting to {action}: {error}"
        raise ChatApiAuthError(msg)
    if error == _RATE_LIMITED_ERROR:
        msg = f"Slack rate-limited while attempting to {action}"
        raise ChatApiRateLimitError(msg)
    msg = f"Slack failed to {action}: {error}"
    raise ChatApiError(msg)


def _parse[M: BaseModel](data: object, model: type[M], *, what: str) -> M:
    """Validate ``data`` against ``model`` or raise a typed chat error.

    Returns:
        The validated model instance.

    Raises:
        ChatApiError: When ``data`` does not satisfy ``model``.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        logger.warning(CHAT_API_ENVELOPE_FAILED, what=what, reason="parse_failed")
        msg = f"malformed Slack {what} response"
        raise ChatApiError(msg) from exc


def _messages_from(data: Mapping[str, object]) -> tuple[ChatMessage, ...]:
    raw = data.get("messages")
    entries = raw if isinstance(raw, list) else []
    return tuple(
        _message_from(_parse(item, _SlMessage, what="message")) for item in entries
    )


def _message_from(model: _SlMessage) -> ChatMessage:
    return ChatMessage(
        ts=model.ts, author=model.user, text=model.text, thread_ts=model.thread_ts
    )


def _channel_from(model: _SlChannel) -> ChatChannel:
    return ChatChannel(
        id=NotBlankStr(model.id or "unknown"),
        name=model.name,
        is_private=model.is_private,
        is_member=model.is_member,
    )


def _user_from(model: _SlUser) -> ChatUser:
    return ChatUser(
        id=NotBlankStr(model.id or "unknown"),
        name=model.name,
        real_name=model.real_name,
        email=model.profile.email if model.profile is not None else "",
        is_bot=model.is_bot,
    )


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = ["SlackChatClient"]

# module-kind: service
"""Out-of-band secret-capture transport.

Captures a credential (a token, a password, an API key) during a
conversational setup flow such that the raw value never enters the chat
turn, the persisted transcript, or an LLM prompt. The masked field posts
the value to a write-only endpoint; the value is written straight into the
encrypted :class:`SecretBackend`, and an opaque, single-use, short-TTL
handle bound to ``(draft_id, field_name)`` is returned. ``connections.create``
later resolves the handle to the value in-process and never in a tool
argument. The handle metadata is process-local (a capture nonce, not durable
domain state): a restart mid-capture drops unconsumed handles and the
operator re-enters, while the value stays encrypted at rest until swept.
"""

import asyncio
import secrets
from datetime import timedelta
from typing import Final, NoReturn
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import SecretCaptureHandleInvalidError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    SECRET_CAPTURE_CONSUMED,
    SECRET_CAPTURE_PURGED,
    SECRET_CAPTURE_REJECTED,
    SECRET_CAPTURE_STORED,
)
from synthorg.persistence.secret_backends.protocol import SecretBackend

logger = get_logger(__name__)

DEFAULT_SECRET_CAPTURE_TTL_SECONDS: Final[int] = 600
_HANDLE_TOKEN_BYTES: Final[int] = 32


class SecretCaptureHandle(BaseModel):
    """Metadata for one captured secret (never carries the value).

    Bound to ``(draft_id, field_name)`` so a handle captured for one field
    of one setup draft cannot be replayed for another. ``conversation_id``
    is recorded for audit only.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    handle_id: NotBlankStr
    secret_id: NotBlankStr
    draft_id: NotBlankStr
    field_name: NotBlankStr
    secret_kind: NotBlankStr
    conversation_id: NotBlankStr | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime


class SecretCaptureService:
    """Process-local, single-use, TTL-bounded secret-capture store.

    Args:
        secret_backend: Where the raw value is written (encrypted at rest).
        clock: Time source (injected in tests).
        ttl_seconds: Handle lifetime before it expires and is swept.
    """

    def __init__(
        self,
        *,
        secret_backend: SecretBackend,
        clock: Clock | None = None,
        ttl_seconds: int = DEFAULT_SECRET_CAPTURE_TTL_SECONDS,
    ) -> None:
        self._backend = secret_backend
        self._clock: Clock = clock or SystemClock()
        self._ttl_seconds = ttl_seconds
        self._handles: dict[str, SecretCaptureHandle] = {}
        self._lock = asyncio.Lock()

    async def capture(
        self,
        *,
        draft_id: NotBlankStr,
        field_name: NotBlankStr,
        secret_kind: NotBlankStr,
        value: str,
        conversation_id: NotBlankStr | None = None,
    ) -> NotBlankStr:
        """Store ``value`` out of band and return an opaque single-use handle.

        The value is written to the secret backend immediately and never
        logged; only capture *metadata* (draft, field, kind) is recorded.

        Returns:
            The opaque handle id the caller passes to ``connections.create``.
        """
        secret_id = NotBlankStr(f"seccap-{uuid4()}")
        await self._store_value(secret_id, value)
        now = self._clock.now()
        handle = SecretCaptureHandle(
            handle_id=NotBlankStr(f"sech_{secrets.token_urlsafe(_HANDLE_TOKEN_BYTES)}"),
            secret_id=secret_id,
            draft_id=draft_id,
            field_name=field_name,
            secret_kind=secret_kind,
            conversation_id=conversation_id,
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        async with self._lock:
            self._handles[handle.handle_id] = handle
        logger.info(
            SECRET_CAPTURE_STORED,
            draft_id=draft_id,
            field=field_name,
            secret_kind=secret_kind,
        )
        return handle.handle_id

    async def consume(
        self,
        *,
        handle_id: str,
        draft_id: str,
        field_name: str,
    ) -> str:
        """Resolve a handle to its raw value exactly once.

        Atomically removes the handle (single-use), verifies it is not
        expired and its ``(draft_id, field_name)`` binding matches, then
        returns the value and deletes the backing secret.

        Returns:
            The raw captured value.

        Raises:
            SecretCaptureHandleInvalidError: If the handle is missing,
                expired, already consumed, or bound to a different
                draft/field.
        """
        async with self._lock:
            handle = self._handles.pop(handle_id, None)
        if handle is None:
            self._reject("unknown_or_consumed", draft_id, field_name)
        now = self._clock.now()
        if handle.expires_at <= now:
            await self._delete_secret(handle.secret_id)
            self._reject("expired", draft_id, field_name)
        if handle.draft_id != draft_id or handle.field_name != field_name:
            # A wrong-binding attempt destroys the handle so it cannot be
            # retried against its real binding (single-use, replay-safe).
            await self._delete_secret(handle.secret_id)
            self._reject("binding_mismatch", draft_id, field_name)
        value_bytes = await self._retrieve_value(handle.secret_id)
        await self._delete_secret(handle.secret_id)
        if value_bytes is None:
            self._reject("value_missing", draft_id, field_name)
        logger.info(
            SECRET_CAPTURE_CONSUMED,
            draft_id=draft_id,
            field=field_name,
            secret_kind=handle.secret_kind,
        )
        return value_bytes.decode("utf-8")

    async def purge_expired(self) -> int:
        """Sweep expired handles and delete their backing secrets.

        Returns:
            The number of expired handles purged.
        """
        now = self._clock.now()
        async with self._lock:
            expired = tuple(
                handle for handle in self._handles.values() if handle.expires_at <= now
            )
            for handle in expired:
                self._handles.pop(handle.handle_id, None)
        for handle in expired:
            await self._delete_secret(handle.secret_id)
        if expired:
            logger.info(SECRET_CAPTURE_PURGED, count=len(expired))
        return len(expired)

    def _reject(self, reason: str, draft_id: str, field_name: str) -> NoReturn:
        """Log a uniform rejection and raise the opaque handle error.

        Raises:
            SecretCaptureHandleInvalidError: Always.
        """
        logger.warning(
            SECRET_CAPTURE_REJECTED,
            reason=reason,
            draft_id=draft_id,
            field=field_name,
        )
        raise SecretCaptureHandleInvalidError

    async def _store_value(self, secret_id: NotBlankStr, value: str) -> None:
        """Write the raw value to the secret backend (value never logged)."""
        await self._backend.store(secret_id, value.encode("utf-8"))

    async def _retrieve_value(self, secret_id: NotBlankStr) -> bytes | None:
        """Retrieve the raw value bytes from the secret backend.

        Returns:
            The stored bytes, or ``None`` when the backing secret is absent.
        """
        return await self._backend.retrieve(secret_id)

    async def _delete_secret(self, secret_id: NotBlankStr) -> None:
        """Best-effort delete of a backing secret (a leak is logged, not fatal)."""
        try:
            await self._backend.delete(secret_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; best-effort cleanup
            reraise_critical(exc)
            logger.warning(
                SECRET_CAPTURE_REJECTED,
                reason="secret_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = [
    "DEFAULT_SECRET_CAPTURE_TTL_SECONDS",
    "SecretCaptureHandle",
    "SecretCaptureService",
]

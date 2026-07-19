# module-kind: service
"""Out-of-band secret-capture transport.

Captures a credential (a token, a password, an API key) during a
conversational setup flow such that the raw value never enters the chat
turn, the persisted transcript, or an LLM prompt. The masked field posts
the value to a write-only endpoint; the value is written straight into the
encrypted :class:`SecretBackend`, and an opaque, single-use, short-TTL
handle bound to ``(draft_id, field_name)`` is returned. ``connections.create``
later resolves the handle to the value in-process and never in a tool
argument.

The handle metadata is process-local (a capture nonce, not durable domain
state): ``connections.create`` resolves a handle in the same process that
captured it, so the handle only needs to outlive the setup turn. A periodic
sweep (``purge_expired``, wired by ``secret_capture_cleanup``) deletes the
backing secret of any handle the operator abandons past its TTL, so the store
does not grow without bound. A restart mid-capture drops the in-memory handle;
its short-TTL encrypted blob is the bounded residual (the backend exposes no
prefix scan to reclaim it, and the operator re-enters the value).
"""

import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Final, NamedTuple, NoReturn, Self
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import SecretCaptureHandleInvalidError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    SECRET_CAPTURE_CONSUMED,
    SECRET_CAPTURE_ORPHANED,
    SECRET_CAPTURE_PURGED,
    SECRET_CAPTURE_REJECTED,
    SECRET_CAPTURE_REQUESTED,
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
    is carried for audit correlation and logged on capture/consume.

    The single-use / expiry / binding invariants are owned by this type via
    :meth:`is_expired` and :meth:`matches_binding` so every consumer applies
    the same rule; :class:`SecretCaptureService` holds the mutable single-use
    registry (the pop-under-lock), but the predicates live here.
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

    @model_validator(mode="after")
    def _expiry_after_creation(self) -> Self:
        """Reject a handle whose TTL window is empty or inverted.

        Returns:
            ``self`` when ``expires_at`` is strictly after ``created_at``.

        Raises:
            ValueError: If ``expires_at`` is not after ``created_at``.
        """
        if self.expires_at <= self.created_at:
            msg = "SecretCaptureHandle.expires_at must be after created_at"
            raise ValueError(msg)
        return self

    def is_expired(self, now: datetime) -> bool:
        """Whether the handle's TTL has elapsed at ``now``.

        Returns:
            ``True`` when ``now`` is at or past ``expires_at``.
        """
        return self.expires_at <= now

    def matches_binding(self, *, draft_id: str, field_name: str) -> bool:
        """Whether the handle is bound to exactly this draft + field.

        Returns:
            ``True`` when both the draft id and field name match.
        """
        return self.draft_id == draft_id and self.field_name == field_name


class PendingSecretCapture(BaseModel):
    """One masked-field capture the operator console still needs from the user.

    Emitted by the ``connections.request_secret_capture`` tool when the console
    reaches a secret field during an in-chat setup flow: it carries no value,
    only enough for the dashboard to render the right masked input and post the
    value out of band to the capture endpoint under this ``draft_id``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    draft_id: NotBlankStr
    connection_type: NotBlankStr
    field_name: NotBlankStr
    secret_kind: NotBlankStr
    label: NotBlankStr | None = None


class _PendingEntry(NamedTuple):
    """A pending capture with the time it was registered, for TTL sweeping."""

    capture: PendingSecretCapture
    registered_at: datetime


class SecretCaptureService:
    """Process-local, single-use, TTL-bounded secret-capture store.

    Also holds the transient per-draft queue of *pending* capture requests the
    operator console raised this turn (see :meth:`register_pending` /
    :meth:`take_pending`), so the in-chat flow can surface the masked fields the
    console asked for without the raw value ever entering the turn.

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
        # Per-draft pending capture requests, deduplicated by field name so a
        # re-asked field replaces rather than stacks. Process-local and
        # consume-on-read: read within the same turn the console raised them.
        # Each entry carries its registration time so an abandoned draft (the
        # console raised a capture but the turn errored before ``take_pending``)
        # is swept on the same TTL as ``_handles`` rather than lingering.
        self._pending: dict[str, dict[str, _PendingEntry]] = {}
        # Constructed lazily on first async use so the lock binds to the
        # running loop then, not to whichever loop happened to be current at
        # construction (which breaks across pytest-asyncio per-test loops and
        # any construct-on-one-loop / use-on-another restart path).
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the single-use registry lock, binding it to the live loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

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
        # The value is now durable; a cancellation before the handle is
        # registered would orphan it, so roll the stored secret back on any
        # exit that fails to register (shielded so the cleanup itself is not
        # cancelled).
        try:
            now = self._clock.now()
            handle = SecretCaptureHandle(
                handle_id=NotBlankStr(
                    f"sech_{secrets.token_urlsafe(_HANDLE_TOKEN_BYTES)}"
                ),
                secret_id=secret_id,
                draft_id=draft_id,
                field_name=field_name,
                secret_kind=secret_kind,
                conversation_id=conversation_id,
                created_at=now,
                expires_at=now + timedelta(seconds=self._ttl_seconds),
            )
            async with self._get_lock():
                self._handles[handle.handle_id] = handle
        except BaseException:
            await asyncio.shield(self._delete_secret(secret_id))
            raise
        logger.info(
            SECRET_CAPTURE_STORED,
            draft_id=draft_id,
            field=field_name,
            secret_kind=secret_kind,
            conversation_id=conversation_id,
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
        async with self._get_lock():
            handle = self._handles.pop(handle_id, None)
        if handle is None:
            self._reject("unknown_or_consumed", draft_id, field_name)
        # The handle is now ours alone (single-use pop). Guarantee its backing
        # secret is deleted exactly once however this exits -- success,
        # rejection (expired / wrong binding / missing value), or a
        # cancellation between retrieve and delete -- so no branch orphans it.
        try:
            now = self._clock.now()
            if handle.is_expired(now):
                self._reject("expired", draft_id, field_name)
            if not handle.matches_binding(draft_id=draft_id, field_name=field_name):
                # A wrong-binding attempt still destroys the handle (in the
                # finally) so it cannot be retried against its real binding.
                self._reject("binding_mismatch", draft_id, field_name)
            value_bytes = await self._retrieve_value(handle.secret_id)
            if value_bytes is None:
                self._reject("value_missing", draft_id, field_name)
            logger.info(
                SECRET_CAPTURE_CONSUMED,
                draft_id=draft_id,
                field=field_name,
                secret_kind=handle.secret_kind,
                conversation_id=handle.conversation_id,
            )
            return value_bytes.decode("utf-8")
        finally:
            await asyncio.shield(self._delete_secret(handle.secret_id))

    async def purge_expired(self) -> int:
        """Sweep expired handles and delete their backing secrets.

        Returns:
            The number of expired handles purged.
        """
        now = self._clock.now()
        async with self._get_lock():
            expired = tuple(
                handle for handle in self._handles.values() if handle.is_expired(now)
            )
            for handle in expired:
                self._handles.pop(handle.handle_id, None)
        self._sweep_pending(now)
        for handle in expired:
            await self._delete_secret(handle.secret_id)
        if expired:
            logger.info(SECRET_CAPTURE_PURGED, count=len(expired))
        return len(expired)

    def _sweep_pending(self, now: datetime) -> None:
        """Drop pending capture requests older than the TTL (and empty drafts).

        The pending queue is value-free (only field metadata), so an abandoned
        draft leaks no secret, but without a sweep it grows unboundedly under
        repeated turn failures in a long-lived process. Consume-on-read, so a
        completed flow clears itself; this only reclaims drafts the operator
        never finished.
        """
        cutoff = now - timedelta(seconds=self._ttl_seconds)
        for draft_id in tuple(self._pending):
            fields = self._pending[draft_id]
            for field_name in tuple(fields):
                if fields[field_name].registered_at <= cutoff:
                    del fields[field_name]
            if not fields:
                del self._pending[draft_id]

    def register_pending(self, pending: PendingSecretCapture) -> None:
        """Record a masked-field capture the console still needs this turn.

        Deduplicated by ``(draft_id, field_name)`` so a field the console asks
        for twice is surfaced once. Synchronous (no I/O), so it is atomic under
        the event loop and needs no lock. The registration time is stamped so
        :meth:`purge_expired` can sweep a draft the operator never completed.
        """
        entry = _PendingEntry(capture=pending, registered_at=self._clock.now())
        self._pending.setdefault(pending.draft_id, {})[pending.field_name] = entry
        logger.info(
            SECRET_CAPTURE_REQUESTED,
            draft_id=pending.draft_id,
            field=pending.field_name,
            connection_type=pending.connection_type,
        )

    def take_pending(self, draft_id: str) -> tuple[PendingSecretCapture, ...]:
        """Return and clear the pending capture requests for a draft.

        Consume-on-read so each console turn surfaces only the fields raised on
        that turn; the dashboard captures them out of band and passes the
        resulting handles back on the next turn.

        Returns:
            The pending requests for ``draft_id`` in field-registration order,
            empty when none are pending.
        """
        return tuple(
            entry.capture for entry in self._pending.pop(draft_id, {}).values()
        )

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
        """Delete a backing secret; a delete failure orphans it (logged ERROR).

        The delete is best-effort so a rejection/consume path is never blocked
        by a backend hiccup, but a failure leaves an encrypted secret orphaned
        in durable storage. That is an operational defect (storage grows,
        secret material lingers), so it is logged at ERROR on a dedicated
        event a leaking backend surfaces in alerting -- not a routine WARNING.
        """
        try:
            await self._backend.delete(secret_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised; best-effort cleanup
            reraise_critical(exc)
            logger.error(
                SECRET_CAPTURE_ORPHANED,
                reason="secret_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def resolve_credential_handles(
    capture: SecretCaptureService,
    *,
    credentials: dict[str, str],
    credential_handles: dict[str, str],
    connection_draft_id: str,
) -> dict[str, str]:
    """Merge inline non-secret fields with out-of-band handle-resolved secrets.

    Each secret handle is consumed exactly once against its
    ``(connection_draft_id, field_name)`` binding, so the raw value only ever
    exists in-process here, never in a request body, a tool argument, or the
    transcript. Callers validate that ``connection_draft_id`` is present before
    calling (the "handles require a draft id" rule is enforced with each
    caller's own boundary error).

    Returns:
        The full credentials mapping ready for ``create_connection``.

    Raises:
        SecretCaptureHandleInvalidError: If a handle is missing, expired,
            already consumed, or bound to a different draft/field.
    """
    resolved = dict(credentials)
    for field_name, handle in credential_handles.items():
        resolved[field_name] = await capture.consume(
            handle_id=handle,
            draft_id=connection_draft_id,
            field_name=field_name,
        )
    return resolved


__all__ = [
    "DEFAULT_SECRET_CAPTURE_TTL_SECONDS",
    "PendingSecretCapture",
    "SecretCaptureHandle",
    "SecretCaptureService",
    "resolve_credential_handles",
]

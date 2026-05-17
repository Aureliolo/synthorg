"""Refresh-token record model.

Lives outside the persistence layer so protocol modules can depend
on the model without importing a backend implementation.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class RefreshRejectReason(StrEnum):
    """Why a ``consume()`` call refused to mint a new lease.

    The repository captures the reason as a typed enum so the auth
    service / controller can emit ``SECURITY_AUTH_REFRESH_REJECTED``
    with the correct ``reason`` field instead of folding every miss
    into a single bucket.
    """

    SESSION_REVOKED = "session_revoked"
    REPLAY_DETECTED = "replay_detected"
    NOT_FOUND_OR_EXPIRED = "not_found_or_expired"


class RefreshConsumeOutcome(BaseModel):
    """Result of a refresh-token consume attempt.

    Exactly one of ``record`` and ``reject_reason`` is populated.
    The model validator below keeps the discriminator honest.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    record: RefreshRecord | None = None
    reject_reason: RefreshRejectReason | None = None

    @model_validator(mode="after")
    def _validate_discriminator(self) -> Self:
        """Enforce: exactly one of ``record`` and ``reject_reason`` is set."""
        if (self.record is None) == (self.reject_reason is None):
            msg = (
                "RefreshConsumeOutcome must populate exactly one of "
                "``record`` or ``reject_reason``"
            )
            raise ValueError(msg)
        return self


class RefreshRecord(BaseModel):
    """A stored refresh token record.

    Attributes:
        token_hash: HMAC-SHA256 hash of the opaque token.
        session_id: Associated JWT session (``jti``).
        user_id: Token owner's user ID.
        expires_at: Expiry timestamp.
        used: Whether the token has been consumed.
        created_at: Creation timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    token_hash: NotBlankStr
    session_id: NotBlankStr
    user_id: NotBlankStr
    expires_at: AwareDatetime
    used: bool = False
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure created_at does not exceed expires_at."""
        if self.created_at > self.expires_at:
            msg = "created_at must not be after expires_at"
            raise ValueError(msg)
        return self

"""Session domain model for JWT session tracking."""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from synthorg.core.auth.roles import HumanRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class Session(BaseModel):
    """An active JWT session.

    Each JWT token issued at login/setup creates a ``Session``
    record.  The ``session_id`` corresponds to the JWT ``jti``
    claim, enabling per-token revocation.

    Attributes:
        session_id: JWT ``jti`` claim (unique token identifier).
        user_id: Owner's user ID.
        username: Owner's login name (denormalized for display).
        role: Owner's role at session creation time.
        ip_address: Client IP at login time.
        user_agent: Client User-Agent header at login time
            (capped at 512 characters).
        created_at: Session creation timestamp.
        last_active_at: Last request timestamp.
        expires_at: JWT expiry timestamp.
        revoked: Whether the session has been revoked.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    session_id: NotBlankStr
    user_id: NotBlankStr
    username: NotBlankStr
    role: HumanRole
    ip_address: str
    user_agent: str
    created_at: AwareDatetime
    last_active_at: AwareDatetime
    expires_at: AwareDatetime
    revoked: bool = False

    @model_validator(mode="after")
    def _validate_temporal_ordering(self) -> Self:
        """Ensure ``created_at <= last_active_at <= expires_at``."""
        if self.created_at > self.expires_at:
            msg = "created_at must not be after expires_at"
            raise ValueError(msg)
        if self.last_active_at < self.created_at:
            msg = "last_active_at must not be before created_at"
            raise ValueError(msg)
        if self.last_active_at > self.expires_at:
            msg = "last_active_at must not be after expires_at"
            raise ValueError(msg)
        return self

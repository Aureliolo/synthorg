"""Pagination cursor configuration.

Carries the HMAC signing key used by :mod:`synthorg.api.cursor`. The key
is loaded from the ``api.pagination.cursor_secret`` setting (masked in
logs) with fallback to ``SYNTHORG_PAGINATION_CURSOR_SECRET`` for
containerised deployments that prefer secret injection via env vars.

When no key is configured, the cursor module generates an ephemeral
per-process key and logs a WARNING once at boot. Ephemeral keys make
pagination tokens invalid across restarts -- operators must set the
key in any deployment that expects stable cursors.
"""

import os

from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_VAR = "SYNTHORG_PAGINATION_CURSOR_SECRET"


class CursorConfig(BaseModel):
    """Pagination cursor configuration.

    Attributes:
        secret: HMAC key for signing pagination cursors. ``None`` means
            an ephemeral random key is generated at process start;
            tokens become invalid across restarts.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    secret: str | None = Field(
        default=None,
        description=(
            "HMAC key for signing pagination cursors. "
            "When None, an ephemeral random key is generated at boot "
            "and a WARNING is logged. Environment variable override: "
            f"{_ENV_VAR}."
        ),
    )

    @field_validator("secret", mode="before")
    @classmethod
    def _reject_blank_secret(cls, value: object) -> object:
        """Reject whitespace-only secrets at the config boundary.

        A blank string would silently route to the ephemeral branch in
        ``CursorSecret.from_config`` -- operators who typo an empty key
        in the setting file would ship with random signing and never
        see a startup warning about the explicit secret being missing.
        Raising here makes the intent unambiguous: pass ``None`` for
        ephemeral, or a real key.

        Returns:
            ``object`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if isinstance(value, str) and not value.strip():
            msg = "cursor secret must not be blank; use None for ephemeral"
            raise ValueError(msg)
        return value

    @classmethod
    def from_env(cls) -> CursorConfig:
        """Build from the ``SYNTHORG_PAGINATION_CURSOR_SECRET`` env var.

        A whitespace-only value is a configuration mistake (the operator
        typed something but it collapsed to empty): reject it explicitly
        so the startup log surfaces the typo, instead of silently
        routing to the ephemeral branch which would quietly invalidate
        pagination tokens on every restart.

        Returns:
            ``CursorConfig`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        raw = os.environ.get(_ENV_VAR)
        if raw is None:
            return cls(secret=None)
        stripped = raw.strip()
        if raw and not stripped:
            msg = (
                f"{_ENV_VAR} is set but contains only whitespace; "
                "unset the variable for ephemeral cursors or set a real key"
            )
            raise ValueError(msg)
        return cls(secret=stripped or None)

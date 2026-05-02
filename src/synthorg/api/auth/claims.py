"""Typed JWT claims contract.

The model is the canonical shape of every JWT minted or accepted by
the API surface. ``decode_token`` returns a :class:`JwtClaims`
instance and ``create_token`` builds one before encoding, so the
middleware and controller-helpers access fields by attribute
(``claims.sub``, ``claims.jti``) rather than by string key.

Two role classes share this single model:

* **User tokens** (issued by the API) carry ``username``, ``role``,
  ``must_change_password``, and ``pwd_sig`` in addition to the
  standard claims.
* **System tokens** (minted by the Go CLI for backend-to-CLI auth)
  carry only the standard claims; the optional user-only fields are
  ``None``.

The middleware enforces the role-specific iss/aud pair and
``pwd_sig`` validation on top of this base contract.
"""

from datetime import datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.api.guards import HumanRole  # noqa: TC001 -- Pydantic field type
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type


class JwtClaims(BaseModel):
    """Typed JWT claim set for both user and system tokens.

    Required claims correspond to the ``require`` list enforced by
    :func:`synthorg.api.auth.service.AuthService.decode_token` so a
    token missing any of them fails decode before reaching the
    boundary helper.

    Attributes:
        iss: Issuer claim (``synthorg-api`` for user tokens,
            ``synthorg-cli`` for system tokens).
        aud: Audience claim (``synthorg-api`` for user tokens,
            ``synthorg-backend`` for system tokens).
        sub: Subject -- user identifier.
        jti: JWT ID -- per-token session identifier used for
            revocation.
        iat: Issued-at timestamp (epoch seconds).
        exp: Expiry timestamp (epoch seconds).
        username: Display name on user tokens; ``None`` on system
            tokens.
        role: Human role (``ceo``, ``manager``, ...) on user tokens;
            ``None`` on system tokens. ``HumanRole.SYSTEM`` is rejected
            at this surface because user-token mint refuses SYSTEM
            users (system tokens are minted by the CLI through a
            different path and do not populate ``role``).
        must_change_password: Forced-change flag on user tokens;
            ``None`` on system tokens.
        pwd_sig: SHA-256 truncation of the stored password hash;
            present only on user tokens.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    iss: NotBlankStr
    aud: NotBlankStr
    sub: NotBlankStr
    jti: NotBlankStr
    iat: int = Field(ge=0, description="Issued-at timestamp (epoch seconds).")
    exp: int = Field(ge=0, description="Expiry timestamp (epoch seconds).")
    username: NotBlankStr | None = None
    role: HumanRole | None = None
    must_change_password: bool | None = None
    pwd_sig: str | None = None

    @field_validator("iat", "exp", mode="before")
    @classmethod
    def _coerce_numeric_date(cls, value: object) -> object:
        """Accept ``datetime`` values from the encode side and convert.

        PyJWT emits NumericDate (epoch seconds) on the wire, so the
        decoded claim is always ``int``. The encode side passes
        ``datetime`` instances; coerce them to epoch seconds so the
        same model serves both directions.
        """
        if isinstance(value, datetime):
            return int(value.timestamp())
        return value

    @model_validator(mode="after")
    def _validate_iat_before_exp(self) -> Self:
        """``iat`` must be strictly less than ``exp``.

        A token issued in the future relative to its own expiry is
        always invalid; PyJWT will accept it on the decode side as
        long as ``exp`` lies in the future, so we enforce the
        invariant here.
        """
        if self.iat >= self.exp:
            msg = "iat must be strictly less than exp"
            raise ValueError(msg)
        return self


__all__ = ["JwtClaims"]

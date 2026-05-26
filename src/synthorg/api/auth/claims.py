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

from synthorg.core.auth.roles import HumanRole
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
    pwd_sig: NotBlankStr | None = None

    @field_validator("iat", "exp", mode="before")
    @classmethod
    def _coerce_numeric_date(cls, value: object) -> object:
        """Accept ``datetime`` values from the encode side and convert.

        PyJWT emits NumericDate (epoch seconds) on the wire, so the
        decoded claim is always ``int``. The encode side passes
        ``datetime`` instances; coerce them to epoch seconds so the
        same model serves both directions.

        Naive (timezone-less) datetimes are rejected: ``.timestamp()``
        interprets a naive value through the host's local timezone, so
        the same ``JwtClaims(iat=datetime(...))`` call on a UTC host
        and a PST host would produce epoch values eight hours apart.
        Across an auth boundary that drifts token lifetimes silently,
        which is exactly the class of cross-environment bug a typed
        contract is supposed to prevent.

        Returns:
            ``object`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                msg = (
                    "NumericDate datetimes must be timezone-aware; "
                    "naive values produce host-TZ-dependent epoch "
                    "seconds and break token lifetime semantics across "
                    "environments"
                )
                raise ValueError(msg)
            return int(value.timestamp())
        return value

    @model_validator(mode="after")
    def _validate_iat_before_exp(self) -> Self:
        """``iat`` must be strictly less than ``exp``.

        A token issued in the future relative to its own expiry is
        always invalid; PyJWT will accept it on the decode side as
        long as ``exp`` lies in the future, so we enforce the
        invariant here.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.iat >= self.exp:
            msg = f"iat ({self.iat}) must be strictly less than exp ({self.exp})"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_token_class_shape(self) -> Self:
        """Reject mixed user/system claim sets.

        The four user-only fields (``username``, ``role``,
        ``must_change_password``, ``pwd_sig``) must arrive as a unit:
        all four set on a user token, all four ``None`` on a system
        token. A partial / mixed set means downstream code that
        assumes user-token semantics could read a ``None`` and either
        crash or, worse, skip a security check. The middleware's
        per-role iss/aud and ``pwd_sig`` validation only protects
        against the well-formed shapes; this validator makes the
        well-formedness itself a model invariant.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        user_fields = (
            self.username,
            self.role,
            self.must_change_password,
            self.pwd_sig,
        )
        any_set = any(field is not None for field in user_fields)
        all_set = all(field is not None for field in user_fields)
        if any_set and not all_set:
            msg = (
                "JwtClaims user-token fields must be all set or all None; "
                "got partial set "
                f"(username={self.username is not None}, "
                f"role={self.role is not None}, "
                f"must_change_password={self.must_change_password is not None}, "
                f"pwd_sig={self.pwd_sig is not None})"
            )
            raise ValueError(msg)
        if self.role is HumanRole.SYSTEM:
            msg = (
                "JwtClaims.role cannot be HumanRole.SYSTEM; system tokens "
                "carry no role claim"
            )
            raise ValueError(msg)
        return self


__all__ = ["JwtClaims"]

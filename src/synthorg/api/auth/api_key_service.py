# module-kind: service
"""API-key management service: issue, list, and revoke API keys.

Wraps :class:`ApiKeyRepository` with the audit emissions and
authorisation rules the persistence layer must not own. Issuance and
revocation emit signed ``security.api_key.*`` audit events AFTER the
persistence write (the persistence boundary forbids repositories
emitting ``security.*`` events). Keys are stored hash-only; the
plaintext is returned exactly once at issuance and never persisted.
"""

from datetime import datetime
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.api.auth.service import AuthService
from synthorg.core.auth.models import ApiKey, AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.domain_errors import ApiKeyNotFoundError, ForbiddenError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_API_KEY_ISSUED,
    SECURITY_API_KEY_REVOKED,
    SECURITY_AUTH_FAILED,
)
from synthorg.persistence.user_protocol import (
    ApiKeyFilterSpec,
    ApiKeyRepository,
)

logger = get_logger(__name__)

#: API-key-issuable roles in ascending seniority. An issuer may mint a
#: key whose role is at or below their own position in this tuple; rank
#: is the index, so no magic-number thresholds are needed. ``SYSTEM`` is
#: deliberately absent: the internal system identity is never issuable
#: through the user-facing key surface.
_ROLE_SENIORITY: tuple[HumanRole, ...] = (
    HumanRole.OBSERVER,
    HumanRole.BOARD_MEMBER,
    HumanRole.PAIR_PROGRAMMER,
    HumanRole.MANAGER,
    HumanRole.CEO,
)


def _may_issue_role(*, issuer: HumanRole, target: HumanRole) -> bool:
    """Return whether ``issuer`` may mint a key carrying ``target`` role.

    Returns:
        ``True`` when both roles are issuable and the target's seniority
        does not exceed the issuer's; ``False`` otherwise (incl. any
        non-issuable role such as ``SYSTEM``).
    """
    if target not in _ROLE_SENIORITY or issuer not in _ROLE_SENIORITY:
        return False
    return _ROLE_SENIORITY.index(target) <= _ROLE_SENIORITY.index(issuer)


class ApiKeyView(BaseModel):
    """Safe projection of an :class:`ApiKey` (never carries the hash)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    name: NotBlankStr
    role: HumanRole
    user_id: NotBlankStr
    created_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    revoked: bool = False

    @classmethod
    def from_model(cls, api_key: ApiKey) -> ApiKeyView:
        """Project a persisted key, dropping ``key_hash``.

        Returns:
            The hash-free view.
        """
        return cls(
            id=api_key.id,
            name=api_key.name,
            role=api_key.role,
            user_id=api_key.user_id,
            created_at=api_key.created_at,
            expires_at=api_key.expires_at,
            revoked=api_key.revoked,
        )


class IssuedApiKey(BaseModel):
    """Issuance result: the safe view plus the one-time plaintext key."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    view: ApiKeyView = Field(description="Hash-free metadata for the new key")
    plaintext: NotBlankStr = Field(
        repr=False,
        description="The raw key, returned exactly once and never persisted",
    )


class ApiKeyService:
    """Issue, list, and revoke API keys with audit + authorisation.

    Args:
        api_keys: The API-key repository.
        auth_service: Provides cryptographic key generation + hashing.
        clock: Time seam for ``created_at`` (tests inject ``FakeClock``).
    """

    def __init__(
        self,
        *,
        api_keys: ApiKeyRepository,
        auth_service: AuthService,
        clock: Clock | None = None,
    ) -> None:
        self._api_keys = api_keys
        self._auth_service = auth_service
        self._clock = clock or SystemClock()

    async def issue(
        self,
        *,
        owner: AuthenticatedUser,
        name: str,
        role: HumanRole,
        expires_at: datetime | None = None,
    ) -> IssuedApiKey:
        """Mint a new API key owned by ``owner`` and emit the audit event.

        Args:
            owner: The authenticated caller who will own the key.
            name: Human-readable label for the key.
            role: Access-control role the key will carry.
            expires_at: Optional expiry (timezone-aware).

        Returns:
            The :class:`IssuedApiKey` carrying the one-time plaintext.

        Raises:
            ForbiddenError: When ``role`` exceeds the owner's seniority
                or is not issuable (e.g. ``SYSTEM``).
            ValueError: When ``expires_at`` is a naive datetime; a naive
                value passes here but raises ``TypeError`` later when the
                SSE revalidation tick compares it against the
                timezone-aware ``clock.now()``, so reject it at the
                boundary instead.
        """
        if expires_at is not None and expires_at.tzinfo is None:
            msg = "expires_at must be timezone-aware"
            raise ValueError(msg)
        if not _may_issue_role(issuer=owner.role, target=role):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="api_key_issue_role_forbidden",
                issuer_user_id=owner.user_id,
                issuer_role=owner.role.value,
                requested_role=role.value,
            )
            msg = "Cannot issue an API key with a role above your own"
            raise ForbiddenError(msg)
        raw_key = self._auth_service.generate_api_key()
        api_key = ApiKey(
            id=str(uuid4()),
            key_hash=self._auth_service.hash_api_key(raw_key),
            name=name,
            role=role,
            user_id=owner.user_id,
            created_at=self._clock.now(),
            expires_at=expires_at,
            revoked=False,
        )
        await self._api_keys.save(api_key)
        logger.info(
            SECURITY_API_KEY_ISSUED,
            key_id=api_key.id,
            key_name=api_key.name,
            role=role.value,
            user_id=owner.user_id,
            issued_by=owner.user_id,
            has_expiry=expires_at is not None,
        )
        return IssuedApiKey(view=ApiKeyView.from_model(api_key), plaintext=raw_key)

    async def list_for_user(self, user_id: str) -> tuple[ApiKeyView, ...]:
        """List a user's API keys (hash-free).

        Returns:
            The user's keys projected to :class:`ApiKeyView`.
        """
        keys = await self._api_keys.query(ApiKeyFilterSpec(user_id=user_id))
        return tuple(ApiKeyView.from_model(k) for k in keys)

    async def revoke(
        self,
        *,
        key_id: str,
        requester: AuthenticatedUser,
    ) -> None:
        """Revoke an API key (owner or CEO) and emit the audit event.

        Idempotent: revoking an already-revoked key is a no-op (no second
        audit emission). A non-owner (non-CEO) caller gets a 404, never a
        403, so key ids cannot be enumerated by status code.

        Raises:
            ApiKeyNotFoundError: When the key is missing or not visible
                to the caller.
        """
        key = await self._api_keys.get(key_id)
        if key is None or (
            key.user_id != requester.user_id and requester.role is not HumanRole.CEO
        ):
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="api_key_revoke_not_found_or_forbidden",
                requester_user_id=requester.user_id,
                requester_role=requester.role.value,
                key_id=key_id,
            )
            raise ApiKeyNotFoundError
        if key.revoked:
            return
        await self._api_keys.save(key.model_copy(update={"revoked": True}))
        logger.info(
            SECURITY_API_KEY_REVOKED,
            key_id=key.id,
            key_name=key.name,
            user_id=key.user_id,
            revoked_by=requester.user_id,
        )

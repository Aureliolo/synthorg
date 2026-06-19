"""Pure helpers for the provider capability mutations.

Extracted from ``service.py`` to keep the module under the project's
800-line ceiling.  Nothing here is bound to ``ProviderManagementService``
state; the credential helpers are stateless transforms over a
discriminated-union DTO and the system-actor constant is a sentinel.
"""

from datetime import UTC, datetime
from typing import Final, assert_never

from synthorg.core.actor_context import current_actor
from synthorg.core.iso_datetime import format_iso_utc
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_VALIDATION_FAILED
from synthorg.providers.errors import ProviderValidationError
from synthorg.providers.management.capability_dtos import (
    CredentialsRotateRequest,
    ProviderAuditActor,
    _ApiKeyRotation,
    _CustomHeaderRotation,
    _OAuthRotation,
    _SubscriptionRotation,
)

logger = get_logger(__name__)

# Bookkeeping actor used when the mutation runs with no actor bound to
# the context seam (background bootstrap, file-driven hot-reload, tests).
# Human mutations bind a HUMAN ``ActorIdentity`` at the HTTP boundary
# (``AuthContextMiddleware``); the audit leaf maps it to a
# ``ProviderAuditActor`` via :func:`provider_actor_from_context`.
SYSTEM_ACTOR = ProviderAuditActor(id="system", label="provider-management")


def provider_actor_from_context() -> ProviderAuditActor:
    """Resolve the provider audit actor from the bound actor seam.

    Reads the :class:`~synthorg.core.actor_context.ActorIdentity` bound
    by ``AuthContextMiddleware`` (or an explicit ``actor_scope``) and
    maps it to a :class:`ProviderAuditActor`. The mapping preserves the
    identity the controller historically threaded: ``id`` is the actor's
    stable id and ``label`` its human-readable name. Background paths
    that bind no actor fall back to :data:`SYSTEM_ACTOR`.

    Returns:
        The actor to attribute the audit row to.
    """
    actor = current_actor()
    if actor is None:
        return SYSTEM_ACTOR
    return ProviderAuditActor(id=actor.actor_id, label=actor.label or actor.actor_id)


_SECRET_SHORT_THRESHOLD: Final[int] = 8


def mask_secret(secret: str) -> str:
    """Mask a secret for safe inclusion in audit logs.

    Returns a string of the form ``"abcd***xyz9"`` with the first 4
    and last 4 characters preserved and the middle replaced by
    ``***``.  Secrets of length 8 or shorter are masked entirely
    (``"********"``) -- at exactly 8 chars the first-4 and last-4
    windows already cover every byte of the secret, so any
    "partial" masking would in fact reveal the whole value.

    Returns:
        A masked string of the form ``"abcd***xyz9"``, or ``"********"``
        for secrets of length 8 or shorter.
    """
    if len(secret) <= _SECRET_SHORT_THRESHOLD:
        return "*" * 8
    return f"{secret[:4]}***{secret[-4:]}"


def credentials_update_fields(
    request: CredentialsRotateRequest,
) -> tuple[dict[str, object], str]:
    """Build the ``ProviderConfig.model_copy(update=...)`` field map.

    Returns ``(field_updates, masked_secret_for_audit)``. The masked
    secret is suitable for direct inclusion in audit-row payloads.

    Returns:
        A ``(field_updates, masked_secret)`` tuple: the update map for
        ``ProviderConfig.model_copy(update=...)`` and an audit-safe
        masked secret.
    """
    match request:
        case _ApiKeyRotation():
            secret = request.api_key.get_secret_value()
            return ({"api_key": secret}, mask_secret(secret))
        case _SubscriptionRotation():
            # ToS re-acceptance is mandatory on subscription rotation;
            # silently rotating with ``tos_accepted=False`` would let
            # callers strip the previously-recorded acceptance timestamp
            # and bypass the contract.
            if not request.tos_accepted:
                msg = (
                    "Subscription rotation requires tos_accepted=true; "
                    "the operator must re-accept the provider's terms"
                )
                exc = ProviderValidationError(msg)
                logger.warning(
                    PROVIDER_VALIDATION_FAILED,
                    auth_type=str(request.auth_type),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise exc
            secret = request.subscription_token.get_secret_value()
            return (
                {
                    "subscription_token": secret,
                    "tos_accepted_at": format_iso_utc(datetime.now(UTC)),
                },
                mask_secret(secret),
            )
        case _CustomHeaderRotation():
            secret = request.custom_header_value.get_secret_value()
            return (
                {
                    "custom_header_name": request.custom_header_name,
                    "custom_header_value": secret,
                },
                mask_secret(secret),
            )
        case _OAuthRotation():
            secret = request.oauth_client_secret.get_secret_value()
            return (
                {
                    "oauth_token_url": request.oauth_token_url,
                    "oauth_client_id": request.oauth_client_id,
                    "oauth_client_secret": secret,
                    "oauth_scope": request.oauth_scope,
                },
                mask_secret(secret),
            )
        case _:  # pragma: no cover - exhaustive over the rotation union
            assert_never(request)

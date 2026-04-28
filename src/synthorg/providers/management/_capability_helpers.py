"""Pure helpers for the provider capability mutations.

Extracted from ``service.py`` to keep the module under the project's
800-line ceiling.  Nothing here is bound to ``ProviderManagementService``
state; the credential helpers are stateless transforms over a
discriminated-union DTO and the system-actor constant is a sentinel.
"""

from datetime import UTC, datetime

from synthorg.api.dto_provider_capabilities import (
    CredentialsRotateRequest,
    ProviderAuditActor,
)
from synthorg.providers.enums import AuthType
from synthorg.providers.errors import ProviderValidationError

# Bookkeeping actor used when the mutation entry point lacks a
# request-scoped actor (background bootstrap, file-driven hot-reload,
# tests).  Real user mutations pass an explicit ``ProviderAuditActor``
# from the controller derived from ``AuthenticatedUser``.
SYSTEM_ACTOR = ProviderAuditActor(id="system", label="provider-management")

_SECRET_SHORT_THRESHOLD = 8


def mask_secret(secret: str) -> str:
    """Mask a secret for safe inclusion in audit logs.

    Returns a string of the form ``"abcd***xyz9"`` with the first 4
    and last 4 characters preserved and the middle replaced by
    ``***``.  Secrets shorter than 8 characters are masked entirely
    (``"********"``) so the prefix/suffix never overlap and reveal
    the whole value.
    """
    if len(secret) < _SECRET_SHORT_THRESHOLD:
        return "*" * 8
    return f"{secret[:4]}***{secret[-4:]}"


def credentials_update_fields(
    request: CredentialsRotateRequest,
) -> tuple[dict[str, object], str]:
    """Build the ``ProviderConfig.model_copy(update=...)`` field map.

    Returns ``(field_updates, masked_secret_for_audit)``.  The masked
    secret is suitable for direct inclusion in audit-row payloads
    per the SEC-1 secret-log rule.
    """
    auth_type = request.auth_type
    if auth_type == AuthType.API_KEY:
        secret = request.api_key.get_secret_value()  # type: ignore[union-attr]
        return ({"api_key": secret}, mask_secret(secret))
    if auth_type == AuthType.SUBSCRIPTION:
        secret = request.subscription_token.get_secret_value()  # type: ignore[union-attr]
        return (
            {
                "subscription_token": secret,
                "tos_accepted_at": (
                    datetime.now(UTC).isoformat()
                    if request.tos_accepted  # type: ignore[union-attr]
                    else None
                ),
            },
            mask_secret(secret),
        )
    if auth_type == AuthType.CUSTOM_HEADER:
        secret = request.custom_header_value.get_secret_value()  # type: ignore[union-attr]
        return (
            {
                "custom_header_name": request.custom_header_name,  # type: ignore[union-attr]
                "custom_header_value": secret,
            },
            mask_secret(secret),
        )
    if auth_type == AuthType.OAUTH:
        secret = request.oauth_client_secret.get_secret_value()  # type: ignore[union-attr]
        return (
            {
                "oauth_token_url": request.oauth_token_url,  # type: ignore[union-attr]
                "oauth_client_id": request.oauth_client_id,  # type: ignore[union-attr]
                "oauth_client_secret": secret,
                "oauth_scope": request.oauth_scope,  # type: ignore[union-attr]
            },
            mask_secret(secret),
        )
    # The discriminated-union covers every AuthType variant supported
    # by the rotation contract, so this line is reachable only if the
    # union is extended without updating this dispatch.
    msg = f"Unsupported auth_type for rotation: {auth_type!r}"  # type: ignore[unreachable]
    raise ProviderValidationError(msg)

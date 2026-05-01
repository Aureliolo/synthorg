"""Provider capability DTOs (HTTP-facing alias).

Source-of-truth lives in
:mod:`synthorg.providers.management.capability_dtos` so the
providers / management subsystem can validate the same shapes
without crossing into the API layer (audit-144).

The underscore-prefixed variants
(``_ApiKeyRotation`` / ``_CustomHeaderRotation`` / ``_OAuthRotation``
/ ``_SubscriptionRotation``) are part of the discriminated union
:class:`CredentialsRotateRequest`; existing tests narrow on the
concrete variant after parse via direct ``isinstance`` checks
(see ``tests/unit/api/test_dto_provider_capabilities.py`` and
``tests/unit/providers/test_capability_mutations.py``).  They stay
in ``__all__`` because callers need them for type narrowing; the
underscore prefix preserves the "treat as implementation detail at
the wire boundary" convention while leaving the symbol importable.
"""

from synthorg.providers.management.capability_dtos import (
    AddModelRequest,
    CredentialsRotateRequest,
    PresetOverride,
    PresetOverrideUpdateRequest,
    ProviderAuditActor,
    ProviderAuditEvent,
    ProviderAuditEventType,
    RateLimitsResponse,
    RateLimitsUpdateRequest,
    SyncModelsRequest,
    SyncModelsResponse,
    _ApiKeyRotation,
    _CustomHeaderRotation,
    _OAuthRotation,
    _SubscriptionRotation,
)

__all__ = [
    "AddModelRequest",
    "CredentialsRotateRequest",
    "PresetOverride",
    "PresetOverrideUpdateRequest",
    "ProviderAuditActor",
    "ProviderAuditEvent",
    "ProviderAuditEventType",
    "RateLimitsResponse",
    "RateLimitsUpdateRequest",
    "SyncModelsRequest",
    "SyncModelsResponse",
    "_ApiKeyRotation",
    "_CustomHeaderRotation",
    "_OAuthRotation",
    "_SubscriptionRotation",
]

"""Provider capability DTOs (HTTP-facing alias).

Source-of-truth lives in
:mod:`synthorg.providers.management.capability_dtos` so the
providers / management subsystem can validate the same shapes
without crossing into the API layer (audit-144).
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

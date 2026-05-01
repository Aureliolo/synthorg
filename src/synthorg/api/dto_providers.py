"""Provider request/response DTOs (HTTP-facing alias).

Source-of-truth lives in :mod:`synthorg.providers.management.dtos`
so the providers / management subsystem can validate the same shapes
without crossing into the API layer (audit-144).
"""

from synthorg.providers.management.dtos import (
    CreateFromPresetRequest,
    CreateProviderRequest,
    DiscoverModelsResponse,
    ProbeLocalResponse,
    ProbePresetResponse,
    ProviderModelResponse,
    ProviderResponse,
    PullModelRequest,
    TestConnectionRequest,
    TestConnectionResponse,
    UpdateModelConfigRequest,
    UpdateProviderRequest,
    to_provider_model_response,
    to_provider_response,
)

__all__ = [
    "CreateFromPresetRequest",
    "CreateProviderRequest",
    "DiscoverModelsResponse",
    "ProbeLocalResponse",
    "ProbePresetResponse",
    "ProviderModelResponse",
    "ProviderResponse",
    "PullModelRequest",
    "TestConnectionRequest",
    "TestConnectionResponse",
    "UpdateModelConfigRequest",
    "UpdateProviderRequest",
    "to_provider_model_response",
    "to_provider_response",
]

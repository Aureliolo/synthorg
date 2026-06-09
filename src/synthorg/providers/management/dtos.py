"""Provider-specific request/response DTOs (public surface).

Source-of-truth lives in the sibling ``_provider_requests`` /
``_provider_responses`` / ``_provider_mappers`` / ``_provider_validators``
modules; this module re-exports the public surface so the canonical
import path ``providers.management.dtos`` stays stable.
"""

from synthorg.providers.management._provider_mappers import (
    to_provider_model_response,
    to_provider_response,
)
from synthorg.providers.management._provider_requests import (
    CreateFromPresetRequest,
    CreateProviderRequest,
    PullModelRequest,
    TestConnectionRequest,
    UpdateModelConfigRequest,
    UpdateProviderRequest,
)
from synthorg.providers.management._provider_responses import (
    DiscoverModelsResponse,
    ProbeLocalResponse,
    ProbePresetResponse,
    ProviderModelResponse,
    ProviderResponse,
    TestConnectionResponse,
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

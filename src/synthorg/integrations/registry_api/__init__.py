"""Container-registry API clients behind one vendor-neutral protocol."""

from synthorg.integrations.registry_api._refs import (
    valid_digest,
    valid_reference,
    valid_repository,
    valid_tag,
)
from synthorg.integrations.registry_api.factory import (
    build_registry_api_client,
    registry_api_supported,
)
from synthorg.integrations.registry_api.protocol import (
    MANIFEST_MEDIA_TYPES,
    ManifestRef,
    RegistryApiClient,
    TagList,
)

__all__ = [
    "MANIFEST_MEDIA_TYPES",
    "ManifestRef",
    "RegistryApiClient",
    "TagList",
    "build_registry_api_client",
    "registry_api_supported",
    "valid_digest",
    "valid_reference",
    "valid_repository",
    "valid_tag",
]

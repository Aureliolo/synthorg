"""Vendor-neutral container-registry client surface.

The governed publish tools speak only this protocol, so a registry preset can
be added without the tool layer, its approval gating, or its tests learning
anything about a specific vendor. A client is bound to one repository at
construction, from the resolved connection record, so an agent cannot name a
repository the operator did not configure.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr

# The manifest media types the client negotiates: OCI image manifest + index
# and their Docker v2 equivalents. Code-defined so a registry cannot steer the
# client onto an unexpected content type.
OCI_MANIFEST_MEDIA_TYPE: str = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX_MEDIA_TYPE: str = "application/vnd.oci.image.index.v1+json"
DOCKER_MANIFEST_MEDIA_TYPE: str = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_MANIFEST_LIST_MEDIA_TYPE: str = (
    "application/vnd.docker.distribution.manifest.list.v2+json"
)
MANIFEST_MEDIA_TYPES: tuple[str, ...] = (
    OCI_MANIFEST_MEDIA_TYPE,
    OCI_INDEX_MEDIA_TYPE,
    DOCKER_MANIFEST_MEDIA_TYPE,
    DOCKER_MANIFEST_LIST_MEDIA_TYPE,
)


class ManifestRef(BaseModel):
    """One manifest, addressed by its content digest.

    ``raw`` carries the exact manifest bytes so a promote can re-PUT them
    byte-for-byte under a new tag; the digest is computed over those bytes,
    so any re-encoding would break it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    digest: NotBlankStr
    media_type: str = ""
    size: int = 0
    raw: bytes = b""


class TagList(BaseModel):
    """The tags currently published under a repository."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    repository: NotBlankStr
    tags: tuple[str, ...] = ()


@runtime_checkable
class RegistryApiClient(Protocol):
    """The registry operations a governed publish tool can perform.

    A client is bound to one repository at construction, from the resolved
    connection record. Keeping the repository off the call surface means an
    agent cannot name a repository the operator did not configure.
    """

    @property
    def repository(self) -> NotBlankStr:
        """The operator-configured repository this client is bound to."""
        ...

    async def list_tags(self, *, limit: int) -> TagList:
        """List the tags published under the bound repository."""
        ...

    async def get_manifest(self, *, reference: NotBlankStr) -> ManifestRef:
        """Fetch a manifest by tag or digest, resolving its content digest."""
        ...

    async def put_manifest(
        self, *, tag: NotBlankStr, raw: bytes, media_type: str
    ) -> ManifestRef:
        """Publish a manifest under a tag, returning the stored digest."""
        ...

    async def blob_exists(self, *, digest: NotBlankStr) -> bool:
        """Whether a blob is already present in the bound repository."""
        ...

    async def upload_blob(self, *, digest: NotBlankStr, data: bytes) -> None:
        """Upload one blob (config or layer) to the bound repository."""
        ...

    async def aclose(self) -> None:
        """Release the underlying transport."""
        ...


__all__ = [
    "DOCKER_MANIFEST_LIST_MEDIA_TYPE",
    "DOCKER_MANIFEST_MEDIA_TYPE",
    "MANIFEST_MEDIA_TYPES",
    "OCI_INDEX_MEDIA_TYPE",
    "OCI_MANIFEST_MEDIA_TYPE",
    "ManifestRef",
    "RegistryApiClient",
    "TagList",
]

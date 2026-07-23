"""OCI Distribution Spec v2 registry client.

Paths are code-defined constants, never configuration: combined with the
pinned ``base_url`` in :class:`BaseRegistryClient`, that is what makes the
egress guarantee structural rather than a policy an operator could mis-set.

One client speaks to every OCI-compliant registry (GHCR, Docker Hub, Quay,
Harbor, GitLab, Artifact Registry); the auth differences are handled by the
bearer-challenge flow in the base, not by vendor branches here.
"""

import hashlib
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.integrations.errors import RegistryApiError
from synthorg.integrations.registry_api._base import BaseRegistryClient
from synthorg.integrations.registry_api._http import raise_for_registry_status
from synthorg.integrations.registry_api.protocol import (
    MANIFEST_MEDIA_TYPES,
    ManifestRef,
    TagList,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    REGISTRY_API_BLOB_UPLOADED,
    REGISTRY_API_MANIFEST_PUBLISHED,
    REGISTRY_API_REQUEST_FAILED,
)

logger = get_logger(__name__)

_TAGS_PATH: Final[str] = "v2/{repo}/tags/list"
_MANIFEST_PATH: Final[str] = "v2/{repo}/manifests/{reference}"
_BLOB_PATH: Final[str] = "v2/{repo}/blobs/{digest}"
_BLOB_UPLOAD_PATH: Final[str] = "v2/{repo}/blobs/uploads/"

_DIGEST_HEADER: Final[str] = "docker-content-digest"
_LOCATION_HEADER: Final[str] = "location"
_OCTET_STREAM: Final[str] = "application/octet-stream"
_NOT_FOUND: Final[int] = 404
_ACCEPT_HEADER: Final[str] = ", ".join(MANIFEST_MEDIA_TYPES)


def _digest_of(content: bytes) -> str:
    """Compute the canonical content-addressable digest of *content*.

    Returns:
        The ``sha256:...`` digest of *content*.
    """
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


class OciRegistryClient(BaseRegistryClient):
    """Client for the OCI Distribution Spec v2 registry API."""

    async def list_tags(self, *, limit: int) -> TagList:
        """List the tags published under the bound repository.

        Args:
            limit: Maximum number of tags to request.

        Returns:
            The tags, newest-first as the registry orders them.

        Raises:
            RegistryApiError: When the registry returns a malformed payload.
        """
        path = _TAGS_PATH.format(repo=self._repository)
        resp = await self._request("GET", path, action="list tags", params={"n": limit})
        raise_for_registry_status(resp, action="list tags")
        payload = self._json_or_raise(resp, action="list tags")
        tags = payload.get("tags") if isinstance(payload, dict) else None
        if tags is None:
            # A repository with no tags may answer with ``{"tags": null}``;
            # treat that as an empty list rather than a malformed payload.
            if isinstance(payload, dict) and "tags" in payload:
                return TagList(repository=self._repository, tags=())
            logger.warning(
                REGISTRY_API_REQUEST_FAILED,
                action="list tags",
                detail="response carried no tag list",
            )
            msg = "registry returned no tag list"
            raise RegistryApiError(msg)
        kept = tuple(str(tag) for tag in tags if isinstance(tag, str))
        return TagList(repository=self._repository, tags=kept)

    async def get_manifest(self, *, reference: NotBlankStr) -> ManifestRef:
        """Fetch a manifest by tag or digest, resolving its content digest.

        Args:
            reference: The tag or digest to read.

        Returns:
            The manifest record, carrying the exact bytes so a promote can
            re-publish them unchanged.
        """
        segment = self._safe_segment(str(reference), field="reference")
        path = _MANIFEST_PATH.format(repo=self._repository, reference=segment)
        resp = await self._request(
            "GET",
            path,
            action="read a manifest",
            headers={"Accept": _ACCEPT_HEADER},
        )
        raise_for_registry_status(resp, action="read a manifest")
        content = resp.content
        return ManifestRef(
            digest=NotBlankStr(resp.headers.get(_DIGEST_HEADER) or _digest_of(content)),
            media_type=resp.headers.get("content-type", ""),
            size=len(content),
            raw=content,
        )

    async def put_manifest(
        self, *, tag: NotBlankStr, raw: bytes, media_type: str
    ) -> ManifestRef:
        """Publish a manifest under a tag, returning the stored digest.

        Args:
            tag: The destination tag.
            raw: The exact manifest bytes to publish.
            media_type: The manifest media type (its ``Content-Type``).

        Returns:
            The stored manifest record.
        """
        segment = self._safe_segment(str(tag), field="tag")
        path = _MANIFEST_PATH.format(repo=self._repository, reference=segment)
        content_type = media_type or MANIFEST_MEDIA_TYPES[0]
        resp = await self._request(
            "PUT",
            path,
            action="publish a manifest",
            headers={"Content-Type": content_type},
            content=raw,
        )
        raise_for_registry_status(resp, action="publish a manifest")
        digest = resp.headers.get(_DIGEST_HEADER) or _digest_of(raw)
        logger.info(
            REGISTRY_API_MANIFEST_PUBLISHED,
            repository=str(self._repository),
            tag=str(tag),
        )
        return ManifestRef(
            digest=NotBlankStr(digest),
            media_type=content_type,
            size=len(raw),
            raw=raw,
        )

    async def blob_exists(self, *, digest: NotBlankStr) -> bool:
        """Whether a blob is already present in the bound repository.

        Args:
            digest: The blob digest.

        Returns:
            ``True`` when the registry reports the blob present, ``False``
            when it reports it absent.
        """
        segment = self._safe_segment(str(digest), field="digest")
        path = _BLOB_PATH.format(repo=self._repository, digest=segment)
        resp = await self._request("HEAD", path, action="check a blob")
        if resp.status_code == _NOT_FOUND:
            return False
        raise_for_registry_status(resp, action="check a blob")
        return True

    async def upload_blob(self, *, digest: NotBlankStr, data: bytes) -> None:
        """Upload one blob to the bound repository (monolithic PUT).

        Args:
            digest: The blob's content digest (verified by the registry).
            data: The blob bytes.

        Raises:
            RegistryApiError: The upload session or the final PUT failed.
        """
        segment = self._safe_segment(str(digest), field="digest")
        start_path = _BLOB_UPLOAD_PATH.format(repo=self._repository)
        started = await self._request("POST", start_path, action="start a blob upload")
        raise_for_registry_status(started, action="start a blob upload")
        location = self._same_host_upload_url(started.headers.get(_LOCATION_HEADER, ""))
        resp = await self._request(
            "PUT",
            location,
            action="upload a blob",
            headers={"Content-Type": _OCTET_STREAM},
            content=data,
            params={"digest": segment},
        )
        raise_for_registry_status(resp, action="upload a blob")
        logger.info(
            REGISTRY_API_BLOB_UPLOADED,
            repository=str(self._repository),
            size=len(data),
        )


def build_oci_client(  # noqa: PLR0913 -- connection facts threaded into one client
    *,
    base_url: str,
    repository: NotBlankStr,
    username: str,
    token: str,
    timeout: float,
    auth_host: str,
) -> OciRegistryClient:
    """Construct an :class:`OciRegistryClient` (the factory entry point).

    Returns:
        The configured client.
    """
    return OciRegistryClient(
        api_base_url=base_url,
        repository=repository,
        username=username,
        token=token,
        timeout=timeout,
        auth_host=auth_host,
    )


__all__ = ["OciRegistryClient", "build_oci_client"]

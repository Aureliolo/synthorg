"""Upload a workspace-built OCI image layout to the registry.

The "agent builds directly" method: the coding harness built an OCI image
layout in its run workspace, and the host-side tool reads it from the
workspace mount (never through the MCP command body) and uploads its blobs +
manifest to the registry with brokered credentials. Credentials never enter
the sandbox; the bytes travel host-side only.

The layout is agent-controlled input, so it is path-guarded against the
workspace root, size-capped, and every blob is verified against its declared
digest before it is uploaded.
"""

import asyncio
import json
from pathlib import Path
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.integrations.registry_api import (
    RegistryApiClient,
    digest_matches,
    valid_digest,
)
from synthorg.integrations.registry_api.protocol import (
    DOCKER_MANIFEST_LIST_MEDIA_TYPE,
    DOCKER_MANIFEST_MEDIA_TYPE,
    OCI_INDEX_MEDIA_TYPE,
    OCI_MANIFEST_MEDIA_TYPE,
)
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    PUBLISH_TOOL_PUBLISHED,
    PUBLISH_TOOL_SOURCE_INVALID,
)
from synthorg.tools.file_system import PathValidator
from synthorg.tools.publish.errors import PublishSourceError
from synthorg.tools.publish.strategies.protocol import PublishOutcome, PublishRequest

logger = get_logger(__name__)

_METHOD: Final[str] = "workspace_push"
_INDEX_FILE: Final[str] = "index.json"
_BLOBS_DIR: Final[str] = "blobs"
_IMAGE_MANIFEST_TYPES: Final[frozenset[str]] = frozenset(
    {OCI_MANIFEST_MEDIA_TYPE, DOCKER_MANIFEST_MEDIA_TYPE}
)
_INDEX_TYPES: Final[frozenset[str]] = frozenset(
    {OCI_INDEX_MEDIA_TYPE, DOCKER_MANIFEST_LIST_MEDIA_TYPE}
)


class _Budget:
    """Tracks the cumulative bytes read against the per-call image cap."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0

    def consume(self, size: int) -> None:
        """Charge *size* bytes, raising when the image cap is exceeded.

        Raises:
            PublishSourceError: When the running total exceeds the cap.
        """
        self._used += size
        if self._used > self._limit:
            logger.warning(
                PUBLISH_TOOL_SOURCE_INVALID,
                method=_METHOD,
                detail="image layout exceeds the image size cap",
                limit=self._limit,
            )
            msg = (
                "image layout exceeds the configured image size cap "
                f"({self._limit} bytes)"
            )
            raise PublishSourceError(msg)


def _descriptor(raw: object) -> tuple[NotBlankStr, str, int]:
    """Validate one OCI descriptor, returning its digest, media type, size.

    Returns:
        The ``(digest, media_type, size)`` triple.

    Raises:
        PublishSourceError: When the descriptor is malformed or its digest
            is not a well-formed content digest (a path-safety invariant, so
            a crafted digest cannot escape the blobs directory).
    """
    if not isinstance(raw, dict):
        msg = "image layout contains a malformed descriptor"
        raise PublishSourceError(msg)
    digest = raw.get("digest")
    media_type = raw.get("mediaType")
    size = raw.get("size", 0)
    if not isinstance(digest, str) or not valid_digest(digest):
        msg = "image layout descriptor has an invalid digest"
        raise PublishSourceError(msg)
    if not isinstance(media_type, str) or not media_type:
        msg = "image layout descriptor has no media type"
        raise PublishSourceError(msg)
    return NotBlankStr(digest), media_type, int(size) if isinstance(size, int) else 0


def _contained_regular_file_size(path: Path, root: Path) -> int:
    """Size of *path* when it is a regular file safely inside *root*, else -1.

    Symlinks are resolved and containment under *root* is confirmed, so a
    symlinked layout entry cannot read a file outside the workspace layout
    host-side. Returns ``-1`` for a missing, non-regular, escaping, or
    unreadable path.

    Returns:
        The size in bytes, or ``-1`` when the path is not a safely-contained
        regular file.
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
    except OSError:
        return -1
    if resolved != root_resolved and root_resolved not in resolved.parents:
        return -1
    return resolved.stat().st_size if resolved.is_file() else -1


async def _read_blob(layout_dir: Path, digest: NotBlankStr, budget: _Budget) -> bytes:
    """Read one blob file, size-capping and digest-verifying it.

    Returns:
        The blob bytes.

    Raises:
        PublishSourceError: The blob is missing, escapes the layout, is
            oversized, or its content does not match the declared digest.
    """
    algo, _, hex_digest = str(digest).partition(":")
    blob_path = layout_dir / _BLOBS_DIR / algo / hex_digest
    size = await asyncio.to_thread(_contained_regular_file_size, blob_path, layout_dir)
    if size < 0:
        msg = f"image layout is missing blob {digest}"
        raise PublishSourceError(msg)
    budget.consume(size)
    data: bytes = await asyncio.to_thread(blob_path.read_bytes)
    if not digest_matches(str(digest), data):
        logger.warning(
            PUBLISH_TOOL_SOURCE_INVALID,
            method=_METHOD,
            detail="blob content did not match its declared digest",
        )
        msg = f"image layout blob {digest} does not match its content"
        raise PublishSourceError(msg)
    return data


async def _upload_image_blobs(
    client: RegistryApiClient, layout_dir: Path, manifest_bytes: bytes, budget: _Budget
) -> int:
    """Upload an image manifest's config + layer blobs, skipping present ones.

    Returns:
        The number of blobs uploaded.
    """
    manifest = _parse_json(manifest_bytes)
    descriptors: list[object] = []
    config = manifest.get("config")
    if config is not None:
        descriptors.append(config)
    layers = manifest.get("layers")
    if isinstance(layers, list):
        descriptors.extend(layers)
    uploaded = 0
    for raw in descriptors:
        digest, _, _ = _descriptor(raw)
        if await client.blob_exists(digest=digest):
            continue
        data = await _read_blob(layout_dir, digest, budget)
        await client.upload_blob(digest=digest, data=data)
        uploaded += 1
    return uploaded


def _parse_json(data: bytes) -> dict[str, object]:
    """Parse a manifest / index document, mapping a bad body to a source error.

    Returns:
        The parsed JSON object.

    Raises:
        PublishSourceError: The bytes are not a JSON object.
    """
    try:
        parsed = json.loads(data)
    except ValueError as exc:
        msg = "image layout contains a malformed manifest document"
        raise PublishSourceError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "image layout manifest is not a JSON object"
        raise PublishSourceError(msg)
    return parsed


class WorkspacePushStrategy:
    """Upload a workspace-built OCI image layout and tag it."""

    async def publish(
        self, client: RegistryApiClient, request: PublishRequest
    ) -> PublishOutcome:
        """Read the layout, upload its blobs + manifests, tag the result.

        Args:
            client: The registry client, pinned to the target repository.
            request: The resolved publish inputs.

        Returns:
            The stored publish outcome.

        Raises:
            PublishSourceError: The workspace path escapes the workspace, is
                not an OCI layout, is oversized, or is malformed.
        """
        layout_dir = await asyncio.to_thread(self._resolve_layout, request)
        budget = _Budget(request.max_image_bytes)
        index_bytes = await _read_document(
            layout_dir / _INDEX_FILE, layout_dir, request.max_manifest_bytes
        )
        index = _parse_json(index_bytes)
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or not manifests:
            msg = "image layout index.json lists no manifests"
            raise PublishSourceError(msg)
        uploaded, root_bytes, root_media = await self._push_graph(
            client,
            layout_dir,
            index=index,
            index_bytes=index_bytes,
            manifests=manifests,
            budget=budget,
            manifest_cap=request.max_manifest_bytes,
        )
        stored = await client.put_manifest(
            tag=request.dest_tag, raw=root_bytes, media_type=root_media
        )
        logger.info(
            PUBLISH_TOOL_PUBLISHED,
            method=_METHOD,
            tag=str(request.dest_tag),
            digest=str(stored.digest),
            blobs_uploaded=uploaded,
        )
        return PublishOutcome(
            published_tag=request.dest_tag,
            digest=stored.digest,
            method=_METHOD,
            blobs_uploaded=uploaded,
        )

    @staticmethod
    def _resolve_layout(request: PublishRequest) -> Path:
        """Path-guard the workspace image path to an existing directory.

        Returns:
            The resolved, workspace-contained layout directory.

        Raises:
            PublishSourceError: The path escapes the workspace or is not a
                directory.
        """
        try:
            layout_dir = PathValidator(request.workspace_root).validate(
                request.source_image_path
            )
        except ValueError as exc:
            msg = "source_image_path is not a usable workspace path"
            raise PublishSourceError(msg) from exc
        if not layout_dir.is_dir():
            msg = "source_image_path is not an OCI image layout directory"
            raise PublishSourceError(msg)
        return layout_dir

    @staticmethod
    async def _push_graph(
        client: RegistryApiClient,
        layout_dir: Path,
        *,
        index: dict[str, object],
        index_bytes: bytes,
        manifests: list[object],
        budget: _Budget,
        manifest_cap: int,
    ) -> tuple[int, bytes, str]:
        """Upload every blob + child manifest and return the root to tag.

        A single-image layout tags the image manifest directly; a multi-arch
        layout pushes each child manifest by digest, then tags the index by its
        original on-disk bytes (re-serialising the parsed dict could change the
        byte content, and the index's own content digest is over those bytes).

        Returns:
            ``(blobs_uploaded, root_bytes, root_media_type)``.

        Raises:
            PublishSourceError: A referenced manifest is a nested index (an
                unsupported shape) or the layout is otherwise malformed.
        """
        first_digest, first_media, _ = _descriptor(manifests[0])
        if len(manifests) == 1 and first_media in _IMAGE_MANIFEST_TYPES:
            root_bytes = await _read_manifest_blob(
                layout_dir, first_digest, budget, manifest_cap
            )
            uploaded = await _upload_image_blobs(client, layout_dir, root_bytes, budget)
            return uploaded, root_bytes, first_media
        uploaded = 0
        for raw in manifests:
            digest, media, _ = _descriptor(raw)
            child_bytes = await _read_manifest_blob(
                layout_dir, digest, budget, manifest_cap
            )
            if media in _INDEX_TYPES:
                logger.warning(
                    PUBLISH_TOOL_SOURCE_INVALID,
                    method=_METHOD,
                    detail="layout referenced a nested image index",
                )
                msg = "nested image indexes are not supported"
                raise PublishSourceError(msg)
            uploaded += await _upload_image_blobs(
                client, layout_dir, child_bytes, budget
            )
            await client.put_manifest(tag=digest, raw=child_bytes, media_type=media)
        index_media = index.get("mediaType")
        root_media = (
            index_media
            if isinstance(index_media, str) and index_media
            else OCI_INDEX_MEDIA_TYPE
        )
        return uploaded, index_bytes, root_media


async def _read_manifest_blob(
    layout_dir: Path, digest: NotBlankStr, budget: _Budget, manifest_cap: int
) -> bytes:
    """Read a manifest blob, capping it by the manifest size limit too.

    Returns:
        The manifest bytes.

    Raises:
        PublishSourceError: The manifest exceeds the manifest size cap.
    """
    data = await _read_blob(layout_dir, digest, budget)
    if len(data) > manifest_cap:
        msg = (
            "image layout manifest exceeds the configured manifest size cap "
            f"({manifest_cap} bytes)"
        )
        raise PublishSourceError(msg)
    return data


async def _read_document(path: Path, root: Path, cap: int) -> bytes:
    """Read a top-level layout document (index.json), capped by the manifest cap.

    Returns:
        The document bytes.

    Raises:
        PublishSourceError: The document is missing, escapes the layout, or
            exceeds the cap.
    """
    size = await asyncio.to_thread(_contained_regular_file_size, path, root)
    if size < 0:
        msg = f"image layout is missing {path.name}"
        raise PublishSourceError(msg)
    if size > cap:
        logger.warning(
            PUBLISH_TOOL_SOURCE_INVALID,
            method=_METHOD,
            detail="layout document exceeds the manifest size cap",
            limit=cap,
        )
        msg = f"image layout {path.name} exceeds the manifest size cap ({cap} bytes)"
        raise PublishSourceError(msg)
    return await asyncio.to_thread(path.read_bytes)


__all__ = ["WorkspacePushStrategy"]

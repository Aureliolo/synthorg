"""Promote an existing image digest to a destination tag.

The lightweight publish method: the blobs already live in the target
repository (a CI build pushed them), so publishing is a manifest read by
digest followed by a manifest PUT under the tag. No image bytes move.
"""

from typing import Final

from synthorg.integrations.errors import RegistryApiClientError
from synthorg.integrations.registry_api import RegistryApiClient
from synthorg.observability import get_logger
from synthorg.observability.events.tool import (
    PUBLISH_TOOL_PUBLISHED,
    PUBLISH_TOOL_SOURCE_INVALID,
)
from synthorg.tools.publish.errors import PublishSourceError, PublishToolArgumentError
from synthorg.tools.publish.strategies.protocol import PublishOutcome, PublishRequest

logger = get_logger(__name__)

_METHOD: Final[str] = "digest_promote"


class DigestPromoteStrategy:
    """Repoint a tag at an existing immutable source digest (same repository)."""

    async def publish(
        self, client: RegistryApiClient, request: PublishRequest
    ) -> PublishOutcome:
        """Read the source manifest by digest and re-publish it under the tag.

        Args:
            client: The registry client, pinned to the target repository.
            request: The resolved publish inputs.

        Returns:
            The stored publish outcome.

        Raises:
            PublishToolArgumentError: No source digest was resolved.
            PublishSourceError: The source digest could not be read, or its
                manifest exceeds the manifest-size cap.
        """
        if request.source_digest is None:
            msg = "digest_promote requires a source digest"
            raise PublishToolArgumentError(msg)
        try:
            source = await client.get_manifest(reference=request.source_digest)
        except RegistryApiClientError as exc:
            # A deterministic 4xx on the source read is the agent's problem to
            # correct (typically a 404 for a digest not in the repository, but
            # also a 400/422). Surface it as a source error naming the digest,
            # without asserting the precise status the message cannot know.
            logger.warning(
                PUBLISH_TOOL_SOURCE_INVALID,
                method=_METHOD,
                detail="source digest read was rejected",
            )
            msg = "source digest was rejected or not found in the target repository"
            raise PublishSourceError(msg) from exc
        if source.size > request.max_manifest_bytes:
            logger.warning(
                PUBLISH_TOOL_SOURCE_INVALID,
                method=_METHOD,
                detail="source manifest exceeds the manifest size cap",
                limit=request.max_manifest_bytes,
            )
            msg = (
                "source manifest exceeds the configured manifest size cap "
                f"({request.max_manifest_bytes} bytes)"
            )
            raise PublishSourceError(msg)
        stored = await client.put_manifest(
            tag=request.dest_tag, raw=source.raw, media_type=source.media_type
        )
        logger.info(
            PUBLISH_TOOL_PUBLISHED,
            method=_METHOD,
            tag=str(request.dest_tag),
            digest=str(stored.digest),
        )
        return PublishOutcome(
            published_tag=request.dest_tag,
            digest=stored.digest,
            method=_METHOD,
        )


__all__ = ["DigestPromoteStrategy"]

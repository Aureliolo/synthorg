"""Promote an existing image digest to a destination tag.

The lightweight publish method: the blobs already live in the target
repository (a CI build pushed them), so publishing is a manifest read by
digest followed by a manifest PUT under the tag. No image bytes move.
"""

from synthorg.integrations.errors import RegistryApiClientError
from synthorg.integrations.registry_api import RegistryApiClient
from synthorg.tools.publish.errors import PublishSourceError, PublishToolArgumentError
from synthorg.tools.publish.strategies.protocol import PublishOutcome, PublishRequest

_METHOD: str = "digest_promote"


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
            PublishSourceError: The source digest is not present, or its
                manifest exceeds the manifest-size cap.
        """
        if request.source_digest is None:
            msg = "digest_promote requires a source digest"
            raise PublishToolArgumentError(msg)
        try:
            source = await client.get_manifest(reference=request.source_digest)
        except RegistryApiClientError as exc:
            # A 4xx on the source read is the agent naming a digest that is
            # not in the repository; surface it as a source problem it can
            # correct, not an opaque upstream failure.
            msg = "source digest was not found in the target repository"
            raise PublishSourceError(msg) from exc
        if source.size > request.max_manifest_bytes:
            msg = (
                "source manifest exceeds the configured manifest size cap "
                f"({request.max_manifest_bytes} bytes)"
            )
            raise PublishSourceError(msg)
        stored = await client.put_manifest(
            tag=request.dest_tag, raw=source.raw, media_type=source.media_type
        )
        return PublishOutcome(
            published_tag=request.dest_tag,
            digest=stored.digest,
            method=_METHOD,
        )


__all__ = ["DigestPromoteStrategy"]

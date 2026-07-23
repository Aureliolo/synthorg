"""The pluggable publish-strategy seam.

A publish method (upload a workspace-built image, or promote an existing
digest to a tag) is one :class:`PublishStrategy`. The tool resolves the
strategy from the target's default + the call's inputs, builds a
:class:`PublishRequest` from its validated arguments and runtime limits, and
hands both to the strategy. Adding a method is a new strategy plus a factory
entry; the tool, its governance, and its tests do not change.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.integrations.registry_api import RegistryApiClient


@dataclass(frozen=True)
class PublishRequest:
    """The resolved inputs one publish call operates on.

    Built by the tool from its validated arguments and runtime limits, so a
    strategy never touches the raw argument model or the connection catalog.
    """

    dest_tag: NotBlankStr
    source_digest: NotBlankStr | None
    source_image_path: str
    max_manifest_bytes: int
    max_image_bytes: int
    workspace_root: Path


class PublishOutcome(BaseModel):
    """The result of a successful publish, returned to the harness."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    published_tag: NotBlankStr
    digest: NotBlankStr
    method: str
    blobs_uploaded: int = 0


class PublishStrategy(Protocol):
    """One way an image reaches the registry."""

    async def publish(
        self, client: RegistryApiClient, request: PublishRequest
    ) -> PublishOutcome:
        """Publish under ``request.dest_tag`` and return the stored outcome."""
        ...


__all__ = ["PublishOutcome", "PublishRequest", "PublishStrategy"]

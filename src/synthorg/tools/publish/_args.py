"""Frozen argument models for the governed publish tools.

Each tool dispatches on an ``action`` field. ``target`` names an
operator-allowlisted registry connection and ``reference`` / ``dest_tag`` /
``source_digest`` become OCI reference path segments, so all are validated
here against the reference grammar before they can reach the client.

Note what is deliberately *absent*: there is no channel argument. The channel
decides how hard the push is gated, so it is read from the connection record
rather than accepted from the caller.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.normalization import reject_unsafe_url_segment
from synthorg.core.types import NotBlankStr
from synthorg.integrations.registry_api import (
    valid_digest,
    valid_reference,
    valid_tag,
)

_DEFAULT_TAG_LIMIT: Final[int] = 50
_MAX_TAG_LIMIT: Final[int] = 200


class PublishInspectArgs(BaseModel):
    """Arguments for inspecting a registry (read-only actions)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["list_tags", "get_manifest"]
    target: NotBlankStr = Field(
        description="Name of an operator-allowlisted registry target."
    )
    reference: NotBlankStr | None = Field(
        default=None,
        description="Tag or digest to read. Required for get_manifest.",
    )
    limit: int = Field(
        default=_DEFAULT_TAG_LIMIT,
        ge=1,
        le=_MAX_TAG_LIMIT,
        description="Maximum tags to return for list_tags.",
    )

    @property
    def is_write(self) -> bool:
        """Whether this action mutates the registry.

        Returns:
            Always ``False``: listing tags or reading a manifest observes
            state, it does not change it.
        """
        return False

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """Validate URL-bound values and per-action requirements.

        Returns:
            ``self`` when every value is safe and present.

        Raises:
            ValueError: When a required field is missing or malformed.
        """
        reject_unsafe_url_segment(str(self.target), field="target")
        if self.action == "get_manifest":
            if self.reference is None:
                msg = "reference is required for action 'get_manifest'"
                raise ValueError(msg)
            if not valid_reference(str(self.reference)):
                msg = "reference must be a valid tag or digest"
                raise ValueError(msg)
            reject_unsafe_url_segment(str(self.reference), field="reference")
        return self


class PublishPushArgs(BaseModel):
    """Arguments for publishing an image (the destructive action)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["push"]
    target: NotBlankStr = Field(
        description="Name of an operator-allowlisted registry target."
    )
    dest_tag: NotBlankStr = Field(
        description="The destination tag to publish under (overwrites it)."
    )
    method: Literal["auto", "workspace_push", "digest_promote"] = Field(
        default="auto",
        description=(
            "How the image reaches the registry. 'auto' resolves from the "
            "inputs and the target's default; a source digest promotes a tag, "
            "a workspace image path uploads."
        ),
    )
    source_digest: NotBlankStr | None = Field(
        default=None,
        description="Existing image digest to promote (for digest_promote).",
    )
    source_image_path: str = Field(
        default="",
        description=(
            "Workspace-relative path to a built OCI image layout (for workspace_push)."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Acknowledges that a push overwrites a running tag; a true value "
            "is enforced by the admin guardrail, not this model."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "Why this push is being made; a non-blank value is enforced by "
            "the admin guardrail and recorded in the audit."
        ),
    )

    @property
    def is_write(self) -> bool:
        """Whether this action mutates the registry.

        Returns:
            Always ``True``: a push is the destructive action.
        """
        return True

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """Validate references and per-method source requirements.

        Returns:
            ``self`` when every value is safe and the method's source is
            present and well-formed.

        Raises:
            ValueError: When a reference is malformed or a method's required
                source is absent.
        """
        reject_unsafe_url_segment(str(self.target), field="target")
        if not valid_tag(str(self.dest_tag)):
            msg = "dest_tag must be a valid tag"
            raise ValueError(msg)
        reject_unsafe_url_segment(str(self.dest_tag), field="dest_tag")
        if self.source_digest is not None:
            if not valid_digest(str(self.source_digest)):
                msg = "source_digest must be a valid content digest"
                raise ValueError(msg)
            reject_unsafe_url_segment(str(self.source_digest), field="source_digest")
        if self.method == "digest_promote" and self.source_digest is None:
            msg = "source_digest is required for method 'digest_promote'"
            raise ValueError(msg)
        if self.method == "workspace_push" and not self.source_image_path.strip():
            msg = "source_image_path is required for method 'workspace_push'"
            raise ValueError(msg)
        if self.source_digest is None and not self.source_image_path.strip():
            msg = "a source_digest or a source_image_path is required to push"
            raise ValueError(msg)
        return self


__all__ = ["PublishInspectArgs", "PublishPushArgs"]

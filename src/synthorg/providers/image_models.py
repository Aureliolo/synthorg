# module-kind: code
"""Provider-layer domain models for image-generation requests and responses.

Split from :mod:`synthorg.providers.models` (which stays under its size
budget) but part of the same provider request/response surface. Reuses
:class:`~synthorg.providers.models.TokenUsage` for cost so image-generation
cost flows through the same recording chokepoint as completions.
"""

import copy
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

from .models import TokenUsage

_MAX_IMAGES_PER_REQUEST: Final[int] = 10
"""Upper bound on images requested in a single ``generate_image`` call,
bounding memory and per-call cost."""

_IMAGE_SIZE_PATTERN: Final[str] = r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$"
"""``<width>x<height>`` in pixels, each 1-5 digits (no leading zero)."""


class ImageGenerationConfig(BaseModel):
    """Optional parameters for an image-generation request.

    Attributes:
        size: Output size as ``"<width>x<height>"`` in pixels.
        n: Number of images to generate.
        quality: Provider quality preset (e.g. ``"standard"``/``"hd"``);
            ``None`` lets the provider choose.
        style: Provider style preset; ``None`` lets the provider choose.
        timeout: Request timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    size: str = Field(
        default="1024x1024",
        pattern=_IMAGE_SIZE_PATTERN,
        description="Output size as <width>x<height> in pixels",
    )
    n: int = Field(
        default=1,
        ge=1,
        le=_MAX_IMAGES_PER_REQUEST,
        description="Number of images to generate",
    )
    quality: NotBlankStr | None = Field(
        default=None,
        description="Provider quality preset (None = provider default)",
    )
    style: NotBlankStr | None = Field(
        default=None,
        description="Provider style preset (None = provider default)",
    )
    timeout: float | None = Field(
        default=None,
        gt=0.0,
        description="Request timeout in seconds",
    )


class GeneratedImage(BaseModel):
    """A single generated image returned by a provider.

    Attributes:
        b64_data: Base64-encoded image bytes (no ``data:`` prefix).
        content_type: MIME type of the image.
        revised_prompt: Provider-revised prompt, when the provider
            rewrites the request (``None`` otherwise).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    b64_data: NotBlankStr = Field(
        description="Base64-encoded image bytes (no data: prefix)",
    )
    content_type: str = Field(
        default="image/png",
        description="MIME type of the generated image",
    )
    revised_prompt: str | None = Field(
        default=None,
        description="Provider-revised prompt, when rewritten",
    )


class ImageGenerationResponse(BaseModel):
    """Result of a non-streaming image-generation call.

    Attributes:
        images: The generated images (at least one).
        usage: Cost breakdown (image models bill per image, so token
            counts are zero and ``cost`` carries the per-image price).
        model: Model identifier that served the request.
        provider_request_id: Provider-assigned request ID for debugging.
        provider_metadata: Provider metadata injected by the base class
            (``_synthorg_*`` keys for latency, retry count, retry reason).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    images: tuple[GeneratedImage, ...] = Field(
        min_length=1,
        description="Generated images (at least one)",
    )
    usage: TokenUsage = Field(description="Cost breakdown")
    model: NotBlankStr = Field(description="Model that served the request")
    provider_request_id: NotBlankStr | None = Field(
        default=None,
        description="Provider request ID",
    )
    provider_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Provider metadata injected by the base class (_synthorg_* keys).",
    )

    @model_validator(mode="after")
    def _deep_copy_provider_metadata(self) -> Self:
        """Deep-copy provider_metadata so the frozen model cannot be aliased.

        Returns:
            The instance with ``provider_metadata`` deep-copied.
        """
        object.__setattr__(
            self, "provider_metadata", copy.deepcopy(self.provider_metadata)
        )
        return self

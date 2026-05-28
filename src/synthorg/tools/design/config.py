"""Configuration models for design tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class DesignToolsConfig(BaseModel):
    """Top-level configuration for design tools.

    Attributes:
        image_timeout: Timeout for image generation in seconds.
        max_image_size_bytes: Maximum image output size in bytes.
        asset_storage_path: Optional filesystem path for storing
            generated assets.  ``None`` means in-memory only.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    image_timeout: float = Field(
        default=60.0,
        gt=0,
        le=600.0,
        description="Image generation timeout (seconds)",
    )
    max_image_size_bytes: int = Field(
        default=52_428_800,
        gt=0,
        description="Maximum image output size (bytes, default 50 MB)",
    )
    asset_storage_path: NotBlankStr | None = Field(
        default=None,
        description="Filesystem path for asset storage (None = in-memory)",
    )

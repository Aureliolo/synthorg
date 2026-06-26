"""Typed argument models for design tools.

Tools wired to consume these models:

* :class:`~synthorg.tools.design.image_generator.ImageGeneratorTool`
  -> :class:`ImageGeneratorArgs`
* :class:`~synthorg.tools.design.diagram_generator.DiagramGeneratorTool`
  -> :class:`DiagramGeneratorArgs`
* :class:`~synthorg.tools.design.asset_manager.AssetManagerTool`
  -> :class:`AssetManagerArgs`
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


ImageStyle = Literal["realistic", "sketch", "diagram", "icon"]
ImageQuality = Literal["draft", "standard", "high"]
DiagramType = Literal["flowchart", "sequence", "class", "state", "architecture"]
DiagramOutputFormat = Literal["mermaid", "graphviz"]
AssetAction = Literal["list", "get", "delete", "search"]


class ImageGeneratorArgs(BaseModel):
    """Args for ``image_generator``."""

    model_config = _ARGS_CONFIG

    prompt: NotBlankStr = Field(description="Image description")
    style: ImageStyle = Field(default="realistic", description="Image style")
    width: int = Field(
        default=1024,
        ge=256,
        le=2048,
        description="Image width in pixels",
    )
    height: int = Field(
        default=1024,
        ge=256,
        le=2048,
        description="Image height in pixels",
    )
    quality: ImageQuality = Field(
        default="standard",
        description="Image quality preset",
    )


class DiagramGeneratorArgs(BaseModel):
    """Args for ``diagram_generator``."""

    model_config = _ARGS_CONFIG

    diagram_type: DiagramType = Field(description="Type of diagram to generate")
    description: NotBlankStr = Field(description="Diagram specification")
    title: str = Field(default="", description="Optional diagram title")
    output_format: DiagramOutputFormat = Field(
        default="mermaid",
        description="Output markup format",
    )


class AssetManagerArgs(BaseModel):
    """Args for ``asset_manager``.

    Cross-field invariants enforced at the boundary:

    * ``action='get'`` and ``action='delete'`` require ``asset_id``.
    * ``action='search'`` requires ``query``.

    Enforcing every per-action required field here keeps the same failure
    class (missing required field for the action) on one event path
    (``parse_typed`` -> ``ValidationError``) rather than splitting it
    between the boundary and the tool body.
    """

    model_config = _ARGS_CONFIG

    action: AssetAction = Field(description="Asset operation to perform")
    asset_id: NotBlankStr | None = Field(
        default=None,
        description="Asset identifier (required for get/delete)",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tags for filtering (used with list/search)",
    )
    query: NotBlankStr | None = Field(
        default=None,
        description="Search query for asset metadata (required for search)",
    )

    @model_validator(mode="after")
    def _validate_action_fields(self) -> Self:
        """Enforce each action's required field at the boundary.

        ``get`` / ``delete`` require ``asset_id``; ``search`` requires
        ``query``.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.action in {"get", "delete"} and self.asset_id is None:
            msg = f"asset_id is required when action={self.action!r} (get / delete)"
            raise ValueError(msg)
        if self.action == "search" and self.query is None:
            msg = "query is required when action='search'"
            raise ValueError(msg)
        return self


__all__ = [
    "AssetAction",
    "AssetManagerArgs",
    "DiagramGeneratorArgs",
    "DiagramOutputFormat",
    "DiagramType",
    "ImageGeneratorArgs",
    "ImageQuality",
    "ImageStyle",
]

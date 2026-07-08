"""Design tool event constants."""

from typing import Final

# Image generation
DESIGN_IMAGE_GENERATION_START: Final[str] = "design.image.generation_start"
DESIGN_IMAGE_GENERATION_SUCCESS: Final[str] = "design.image.generation_success"
DESIGN_IMAGE_GENERATION_FAILED: Final[str] = "design.image.generation_failed"
DESIGN_IMAGE_GENERATION_TIMEOUT: Final[str] = "design.image.generation_timeout"

# Diagram generation
DESIGN_DIAGRAM_GENERATION_START: Final[str] = "design.diagram.generation_start"
DESIGN_DIAGRAM_GENERATION_SUCCESS: Final[str] = "design.diagram.generation_success"
DESIGN_DIAGRAM_GENERATION_FAILED: Final[str] = "design.diagram.generation_failed"

# Asset management
DESIGN_ASSET_STORED: Final[str] = "design.asset.stored"
DESIGN_ASSET_RETRIEVED: Final[str] = "design.asset.retrieved"
DESIGN_ASSET_DELETED: Final[str] = "design.asset.deleted"
DESIGN_ASSET_LISTED: Final[str] = "design.asset.listed"
DESIGN_ASSET_SEARCHED: Final[str] = "design.asset.searched"
DESIGN_ASSET_VALIDATION_FAILED: Final[str] = "design.asset.validation_failed"
DESIGN_ASSET_PERSISTED: Final[str] = "design.asset.persisted"
DESIGN_ASSET_PERSIST_FAILED: Final[str] = "design.asset.persist_failed"

# Provider
DESIGN_PROVIDER_NOT_CONFIGURED: Final[str] = "design.provider.not_configured"

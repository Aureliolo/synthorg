"""Template-layer preset domain models.

Moved here from ``synthorg.api.dto_personalities`` so the templates
layer no longer imports from the API layer (audit-144 layer
violation).  The API DTOs continue to consume :class:`PresetSource`
from this module.
"""

from enum import StrEnum


class PresetSource(StrEnum):
    """Origin of a personality preset."""

    BUILTIN = "builtin"
    CUSTOM = "custom"

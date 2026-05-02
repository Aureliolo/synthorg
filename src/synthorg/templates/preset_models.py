"""Template-layer preset domain models.

Lives here so the templates layer does not import from the API layer.
The API DTOs continue to consume :class:`PresetSource` from this
module.
"""

from enum import StrEnum


class PresetSource(StrEnum):
    """Origin of a personality preset."""

    BUILTIN = "builtin"
    CUSTOM = "custom"

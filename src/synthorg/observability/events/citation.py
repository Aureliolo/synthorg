"""Citation tracking event constants."""

from typing import Final

CITATION_ADDED: Final[str] = "citation.added"
CITATION_DEDUPLICATED: Final[str] = "citation.deduplicated"
CITATION_MANAGER_CREATED: Final[str] = "citation.manager.created"
CITATION_HANDOFF_SERIALIZED: Final[str] = "citation.handoff.serialized"
CITATION_HANDOFF_DESERIALIZED: Final[str] = "citation.handoff.deserialized"
CITATION_HANDOFF_INVALID: Final[str] = "citation.handoff.invalid"

"""Citation manager for deduplication and rendering.

Orchestrator-managed citation consolidation. Immutable: each operation
returns a new ``CitationManager``, never mutates in place.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.citation.models import Citation
from synthorg.communication.citation.normalizer import normalize_url
from synthorg.observability import get_logger
from synthorg.observability.events.citation import (
    CITATION_ADDED,
    CITATION_DEDUPLICATED,
    CITATION_HANDOFF_DESERIALIZED,
    CITATION_HANDOFF_SERIALIZED,
    CITATION_MANAGER_CREATED,
)

logger = get_logger(__name__)


class CitationManager(BaseModel):
    """Orchestrator-managed citation consolidation.

    Immutable via ``model_copy(update=...)``: each operation returns
    a new ``CitationManager``.

    Attributes:
        citations: Ordered tuple of tracked citations.
        url_to_number: Mapping from normalized URL to citation number.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    citations: tuple[Citation, ...] = Field(
        default=(),
        description="Ordered tracked citations",
    )
    url_to_number: Mapping[str, int] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Normalized URL to citation number",
    )

    def add(
        self,
        url: str,
        title: str,
        agent_id: str,
        accessed_via: Literal["tool", "memory", "file"] = "tool",
    ) -> CitationManager:
        """Add a citation, deduplicating by normalized URL.

        Args:
            url: Source URL (will be normalized).
            title: Human-readable source title.
            agent_id: Agent that found this source.
            accessed_via: How the source was accessed.

        Returns:
            New ``CitationManager`` with the citation added
            (or unchanged if URL already tracked).
        """
        normalized = normalize_url(url)

        if normalized in self.url_to_number:
            logger.debug(
                CITATION_DEDUPLICATED,
                url=normalized,
                existing_number=self.url_to_number[normalized],
            )
            return self

        number = len(self.citations) + 1
        citation = Citation(
            number=number,
            url=normalized,  # type: ignore[arg-type]  # normalize_url returns str; Pydantic coerces to AnyHttpUrl
            title=title,
            first_seen_at=datetime.now(UTC),
            first_seen_by_agent_id=agent_id,
            accessed_via=accessed_via,
        )

        new_mapping = dict(self.url_to_number)
        new_mapping[normalized] = number

        logger.debug(
            CITATION_ADDED,
            number=number,
            url=normalized,
            agent_id=agent_id,
        )
        return self.model_copy(
            update={
                "citations": (*self.citations, citation),
                "url_to_number": MappingProxyType(new_mapping),
            },
        )

    def render_inline(self, url: str) -> str:
        """Return ``[N]`` for an already-tracked URL.

        Args:
            url: URL to look up (will be normalized).

        Returns:
            Inline citation reference like ``[1]``, or empty
            string if the URL is not tracked.
        """
        normalized = normalize_url(url)
        number = self.url_to_number.get(normalized)
        if number is None:
            return ""
        return f"[{number}]"

    def render_sources_section(self) -> str:
        """Render the final ``## Sources`` markdown block.

        Returns:
            Markdown sources section, or empty string if no
            citations are tracked.
        """
        if not self.citations:
            return ""
        lines = [
            "## Sources",
            "",
            *(f"[{c.number}] {c.title} - {c.url}" for c in self.citations),
        ]
        return "\n".join(lines)

    def to_handoff_payload(self) -> dict[str, object]:
        """Serialize for transport via ``HandoffArtifact.payload``.

        Returns:
            JSON-serializable dict with citation data.
        """
        logger.debug(
            CITATION_HANDOFF_SERIALIZED,
            citation_count=len(self.citations),
        )
        return {
            "citations": [
                {
                    "number": c.number,
                    "url": str(c.url),
                    "title": c.title,
                    "first_seen_at": c.first_seen_at.isoformat(),
                    "first_seen_by_agent_id": c.first_seen_by_agent_id,
                    "accessed_via": c.accessed_via,
                }
                for c in self.citations
            ],
        }

    @classmethod
    def from_handoff_payload(
        cls,
        data: Mapping[str, object],
    ) -> CitationManager:
        """Reconstruct from a ``HandoffArtifact.payload``.

        Args:
            data: Payload dict from ``to_handoff_payload()``.

        Returns:
            Reconstructed ``CitationManager`` with all citations
            and the URL-to-number index rebuilt.

        Raises:
            TypeError: If the payload's ``citations`` is not a list of
                mappings.
        """
        raw_list = data.get("citations", [])
        if not isinstance(raw_list, list):
            msg = "handoff payload 'citations' must be a list"
            raise TypeError(msg)

        citations: list[Citation] = []
        url_map: dict[str, int] = {}
        for raw in raw_list:
            if not isinstance(raw, Mapping):
                msg = "handoff citation entry must be a mapping"
                raise TypeError(msg)
            citation = Citation.model_validate(
                {
                    "number": raw["number"],
                    "url": raw["url"],
                    "title": raw["title"],
                    "first_seen_at": raw["first_seen_at"],
                    "first_seen_by_agent_id": raw["first_seen_by_agent_id"],
                    "accessed_via": raw["accessed_via"],
                },
            )
            citations.append(citation)
            url_map[normalize_url(str(citation.url))] = citation.number

        logger.debug(
            CITATION_HANDOFF_DESERIALIZED,
            citation_count=len(citations),
        )
        result = cls(
            citations=tuple(citations),
            url_to_number=MappingProxyType(url_map),
        )
        logger.debug(CITATION_MANAGER_CREATED, citation_count=len(citations))
        return result

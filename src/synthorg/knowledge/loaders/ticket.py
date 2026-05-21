"""Ticket source loader.

Live ticket ingestion fetches through the governed external-API access
tool (the merged connection catalog: credential brokering, SSRF + DNS
pinning, rate limiting). Wiring that transport into this loader is
deliberately staged after the MVP corpus (repo + PDF + web), so today
the loader raises a clear :class:`KnowledgeSourceUnavailableError`
directing operators to the governed-connection path rather than silently
degrading.
"""

from typing import TYPE_CHECKING

from synthorg.knowledge.errors import KnowledgeSourceUnavailableError

if TYPE_CHECKING:
    from synthorg.knowledge.models import KnowledgeSource, RawDocument


class TicketLoader:
    """Placeholder loader until the governed ticket transport is wired."""

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Reject ticket ingestion until the governed transport is wired."""
        msg = (
            "Live ticket ingestion is not yet wired; it routes through the "
            "governed external-API connection. Ingest the repo, PDF, "
            f"and web sources for now (source {source.source_id!r})."
        )
        raise KnowledgeSourceUnavailableError(msg)

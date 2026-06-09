"""Ticket source loader.

Walks a ticket thread (root comment + replies) through an injected
:class:`TicketFetcher`. The fetcher is the seam through which ticket
data reaches the loader: production wires one that calls the governed
external-API access tool (credential brokering from the connection
catalog, SSRF + DNS pinning, rate limiting, approval gating). Tests
wire a deterministic in-process fetcher.

Each ticket comment becomes one :class:`RawUnit` with a
:class:`TicketLocator`, so retrieval citations resolve to the exact
comment (and char range) the matching chunk came from.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.enums import ContentKind
from synthorg.knowledge.errors import (
    KnowledgeIngestError,
    KnowledgeSourceUnavailableError,
)
from synthorg.knowledge.models import (
    KnowledgeSource,
    RawDocument,
    RawUnit,
    TicketLocator,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import (
    KNOWLEDGE_LOAD_FAILED,
    KNOWLEDGE_SOURCE_LOADED,
)
from synthorg.versioning.hashing import compute_text_hash

logger = get_logger(__name__)


class TicketComment(BaseModel):
    """One comment in a ticket thread, as returned by a :class:`TicketFetcher`.

    Comments are emitted in their original posting order; the loader
    preserves that order in the resulting :class:`RawDocument` so chunk
    indices stay stable across re-ingests (a new comment at the end of
    the thread does not shift earlier chunk identities).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    comment_id: NotBlankStr = Field(description="Stable id within the ticket")
    body: str = Field(description="Comment body, plain text (may be empty)")


class TicketThread(BaseModel):
    """A ticket payload returned by :class:`TicketFetcher.fetch`.

    The loader treats this as opaque ingested content: the fetcher is
    responsible for SSRF / DNS pinning / credential governance, and for
    deciding what counts as one ticket (e.g. the originating issue and
    its replies, but not unrelated cross-links).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ticket_id: NotBlankStr = Field(description="Stable ticket identifier")
    comments: tuple[TicketComment, ...] = Field(
        description="Comments in original posting order (oldest first)",
    )


@runtime_checkable
class TicketFetcher(Protocol):
    """Fetches a ticket thread through a governed transport.

    Implementations:

    * MUST route the network call through the governed external-API
      access path (credential brokering, SSRF + DNS pinning, rate
      limiting), so a malicious ``ticket_uri`` cannot exfiltrate the
      host's internal network.
    * MUST raise on transport-level failures; the loader maps any
      exception to :class:`KnowledgeSourceUnavailableError` so the
      source row is marked ``FAILED`` and a clear cause is logged.
    """

    async def fetch(self, ticket_uri: str) -> TicketThread:
        """Return the ticket thread identified by the provided URI."""
        ...


class TicketLoader:
    """Loads a ticket thread into one :class:`RawUnit` per comment."""

    __slots__ = ("_fetcher",)

    def __init__(self, *, fetcher: TicketFetcher) -> None:
        self._fetcher = fetcher

    async def load(self, source: KnowledgeSource) -> RawDocument:
        """Fetch ``source.uri`` and emit one unit per ticket comment.

        Returns:
            A ``RawDocument`` with one unit per ticket comment.

        Raises:
            KnowledgeSourceUnavailableError: When the ticket fetch fails.
            KnowledgeIngestError: When the fetcher returns a thread for a
                different ticket than requested.
        """
        try:
            thread = await self._fetcher.fetch(source.uri)
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to fetch ticket source {source.source_id!r}"
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise KnowledgeSourceUnavailableError(msg) from exc
        if thread.ticket_id != source.uri:
            # The fetcher returned a thread for a different ticket than
            # the caller asked for; this is a contract violation and
            # would produce mis-attributed citations if accepted.
            msg = (
                f"Ticket fetcher returned ticket_id={thread.ticket_id!r} for "
                f"source uri {source.uri!r}; refusing to ingest mismatched thread"
            )
            logger.warning(
                KNOWLEDGE_LOAD_FAILED,
                source_id=source.source_id,
                error_type="TicketIdMismatch",
                error=msg,
            )
            raise KnowledgeIngestError(msg)
        units = tuple(
            RawUnit(
                text=comment.body,
                locator=TicketLocator(
                    ticket_id=NotBlankStr(thread.ticket_id),
                    comment_id=NotBlankStr(comment.comment_id),
                    char_start=0,
                    char_end=len(comment.body),
                ),
                content_kind=ContentKind.TICKET_THREAD,
            )
            for comment in thread.comments
            if comment.body.strip()
        )
        logger.debug(
            KNOWLEDGE_SOURCE_LOADED,
            source_id=source.source_id,
            source_type=source.source_type.value,
            unit_count=len(units),
        )
        hash_input = "\n".join(f"{c.comment_id}\n{c.body}" for c in thread.comments)
        return RawDocument(
            source_id=source.source_id,
            source_type=source.source_type,
            uri=source.uri,
            title=NotBlankStr(source.title),
            content_hash=compute_text_hash(hash_input),
            units=units,
        )

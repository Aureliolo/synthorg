"""Shipped parsers, one per feed shape a declared source publishes.

A parser turns one source's document into scores and never fetches,
persists or resolves anything: keeping it a pure function of the document
is what lets an operator's uploaded file take exactly the same path as an
automatic refresh, and what lets a feed's shape be tested without a
network.
"""

from datetime import datetime

from synthorg.providers.capability_sources.errors import CapabilitySourceParseError
from synthorg.providers.capability_sources.parsed_feed import ParsedFeed
from synthorg.providers.capability_sources.parsers.epoch import parse_epoch_csv

#: Encoding every text feed this dispatcher decodes. Declared rather than
#: sniffed: a mis-detected encoding corrupts model identifiers, and a
#: corrupted identifier matches nothing and grades nothing.
_TEXT_ENCODING = "utf-8"


def parse_document(
    parser_key: str,
    document: bytes,
    *,
    source_label: str,
    ingested_at: datetime,
) -> ParsedFeed:
    """Run the parser *parser_key* names over *document*.

    Every parser takes bytes here so an operator's uploaded file and an
    automatic fetch reach the same code, with text formats decoded once at
    this boundary rather than in each parser.

    Returns:
        The parsed feed.

    Raises:
        CapabilitySourceParseError: When no parser is registered under
            *parser_key*, or the document cannot be decoded as text for a
            text format. An unknown key is a registry that names a parser
            nobody wrote, so it fails loudly rather than parsing to
            nothing.
    """
    if parser_key == "epoch_csv":
        try:
            text = document.decode(_TEXT_ENCODING)
        except UnicodeDecodeError as exc:
            msg = (
                f"The {source_label} document is not {_TEXT_ENCODING} text. "
                f"The previously ingested scores for this source are unchanged."
            )
            raise CapabilitySourceParseError(msg) from exc
        return parse_epoch_csv(text, source_label=source_label, ingested_at=ingested_at)
    msg = (
        f"No parser is registered under {parser_key!r}, so the source "
        f"{source_label!r} cannot be read."
    )
    raise CapabilitySourceParseError(msg)


__all__ = ["parse_document", "parse_epoch_csv"]

# module-kind: code
"""Errors for the capability-evidence subsystem."""

from typing import ClassVar

from synthorg.core.domain_errors import NotFoundError, ValidationError


class CapabilitySourceUnknownError(NotFoundError):
    """Raised when a request names a source the registry does not declare.

    An unknown source is rejected rather than guessed at. A near-miss
    label silently accepted would file the operator's uploaded scores
    under a source nobody can find them by, which is indistinguishable
    from the upload having been ignored.
    """

    default_message: ClassVar[str] = (
        "No capability source is registered under that label. List the "
        "registered sources and use one of their labels exactly."
    )


class CapabilitySourceParseError(ValidationError):
    """Raised when a source's document cannot be parsed into scores.

    Scoped per source by the caller rather than by carrying one: the ingest
    path catches this, marks the one source it was parsing as failed, and
    leaves every other source's evidence untouched.
    """

    default_message: ClassVar[str] = (
        "The capability document could not be parsed. Its shape does not "
        "match what this source publishes; the previously ingested scores "
        "for this source are unchanged."
    )


class CapabilityFeedTooLargeError(ValidationError):
    """Raised when a feed body exceeds the size a source could plausibly be.

    A body an order of magnitude past anything a shipped source publishes
    is a wrong URL or a redirect to something else entirely, so it is
    refused before parsing rather than after.
    """

    default_message: ClassVar[str] = (
        "The document at this feed URL is far larger than any capability "
        "feed should be, so it was not parsed. Check the URL."
    )


class CapabilityFeedRedirectedError(ValidationError):
    """Raised when a feed URL answers with a redirect.

    The SSRF pre-flight validates the URL it was given, so a redirect
    followed automatically would reach a host nothing checked. Refusing
    it also keeps a moved feed loud: a 3xx body is empty, so following
    nothing and parsing it would report zero rows rather than a broken
    URL.
    """

    default_message: ClassVar[str] = (
        "This feed URL redirects elsewhere, and a redirect target is not "
        "covered by the check that approved the URL, so it was not "
        "followed. Configure the URL the feed now lives at."
    )


__all__ = [
    "CapabilityFeedRedirectedError",
    "CapabilityFeedTooLargeError",
    "CapabilitySourceParseError",
    "CapabilitySourceUnknownError",
]

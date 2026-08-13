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

    Carries the source so a per-source failure stays per-source: the
    ingest path catches this, marks that one source failed, and leaves
    every other source's evidence untouched.
    """

    default_message: ClassVar[str] = (
        "The capability document could not be parsed. Its shape does not "
        "match what this source publishes; the previously ingested scores "
        "for this source are unchanged."
    )


__all__ = [
    "CapabilitySourceParseError",
    "CapabilitySourceUnknownError",
]

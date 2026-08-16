# module-kind: declarative
"""Whether an operator authorised this brief to stand up an initiative.

The spine cannot read charters: they live above it, and the whole point of
:class:`~synthorg.engine.pipeline.models.WorkItem` is that every adapter
feeds one shape. So the question is asked through a port narrow enough to
answer nothing else, and the answer names the condition rather than
returning a bare boolean: "no such charter" and "still a draft" are
different operator actions, and a brief refused without saying which one
sends somebody looking in the wrong place.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable


class CharterAuthorisation(StrEnum):
    """What the charter store says about a brief's named charter."""

    APPROVED = "approved"
    UNKNOWN = "unknown"
    UNDECIDED = "undecided"


@runtime_checkable
class CharterAuthority(Protocol):
    """Resolves a charter id to whether an operator approved it."""

    async def authorisation_of(self, charter_id: str) -> CharterAuthorisation:
        """Return what the charter store says about *charter_id*.

        Args:
            charter_id: The charter a brief names as its authorisation.

        Returns:
            ``APPROVED`` when the row exists and an operator approved it,
            ``UNKNOWN`` when no such charter exists, ``UNDECIDED`` when one
            does and is still drafted or was cancelled.
        """
        ...


__all__ = ["CharterAuthorisation", "CharterAuthority"]

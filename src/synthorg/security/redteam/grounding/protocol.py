"""Grounding-checker protocol.

The protocol is the seam between the gate and any grounding
implementation. The current heuristic implementation runs without
an LLM; a future substrate-backed implementation will resolve each
claim to a source chunk in the knowledge store. The gate does not
change between the two.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from synthorg.security.redteam.grounding.models import UngroundedClaim


@runtime_checkable
class GroundingChecker(Protocol):
    """Inspect deliverable text for claims that lack a traceable source.

    Implementations MUST be deterministic enough to support the
    deterministic simulation harness. The heuristic implementation
    is pure-Python regex; substrate-backed implementations should pin
    their corpus snapshot and any LLM extractor to fixed temperature
    so the harness can replay golden runs.
    """

    async def check(
        self,
        *,
        deliverable_content: NotBlankStr,
        execution_id: NotBlankStr,
        project_id: NotBlankStr | None = None,
    ) -> tuple[UngroundedClaim, ...]:
        """Return zero or more :class:`UngroundedClaim` entries.

        Args:
            deliverable_content: The artifact text under review.
            execution_id: Identifier for the execution that produced
                the deliverable. Implementations may use it for
                caching or diagnostic event correlation.
            project_id: Owning project of the deliverable, when known.
                Substrate-backed implementations scope the corpus search
                to it (project plus global sources); ``None`` searches
                global sources only. The heuristic implementation ignores
                it.

        Returns:
            Tuple of claims that failed grounding. Empty tuple when
            no ungrounded claims were detected.
        """
        ...

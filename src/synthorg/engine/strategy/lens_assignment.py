"""Lens assignment for meeting participants.

Assigns strategic lenses to meeting participants to ensure diverse
perspectives during group decision-making. Multiple assignment strategies
are supported (e.g., diversity-maximizing round-robin).
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger

logger = get_logger(__name__)


@runtime_checkable
class LensAssigner(Protocol):
    """Assign strategic lenses to meeting participants.

    Implementations determine how to distribute available lenses across
    participant IDs, ensuring diverse viewpoints during discussions.
    """

    def assign(
        self,
        participant_ids: tuple[NotBlankStr, ...],
        available_lenses: tuple[NotBlankStr, ...],
    ) -> dict[NotBlankStr, NotBlankStr]:
        """Map each participant to a lens name.

        Args:
            participant_ids: IDs of participating agents.
            available_lenses: Available lens names to assign.

        Returns:
            Dict mapping participant_id -> lens_name.
            Every participant receives a lens (round-robin wrap when
            participants outnumber available lenses).  An empty dict
            is returned when *available_lenses* or *participant_ids*
            is empty.
        """
        ...


class DiversityMaximizingAssigner:
    """Assign lenses to maximize viewpoint diversity.

    Uses round-robin assignment to ensure each participant gets a distinct
    lens when possible. When participants outnumber lenses, lenses wrap
    around. This encourages different agents to approach the problem
    from different angles in different meetings.
    """

    def assign(
        self,
        participant_ids: tuple[NotBlankStr, ...],
        available_lenses: tuple[NotBlankStr, ...],
    ) -> dict[NotBlankStr, NotBlankStr]:
        """Assign lenses via round-robin, wrapping as needed.

        Args:
            participant_ids: IDs of participating agents.
            available_lenses: Available lens names to assign.

        Returns:
            Dict mapping each participant to a lens name.
            If either input is empty, returns empty dict.
        """
        if not participant_ids or not available_lenses:
            return {}

        result: dict[NotBlankStr, NotBlankStr] = {}
        for idx, participant_id in enumerate(participant_ids):
            lens_idx = idx % len(available_lenses)
            assigned_lens = available_lenses[lens_idx]
            result[participant_id] = assigned_lens

        return result

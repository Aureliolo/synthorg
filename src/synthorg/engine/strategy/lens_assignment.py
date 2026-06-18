"""Lens assignment for meeting participants.

Assigns strategic lenses to meeting participants to ensure diverse
perspectives during group decision-making. The meeting orchestrator
consumes assigners structurally via its own ``_LensAssigner`` protocol,
so this module ships the concrete strategy only.
"""

from synthorg.observability import get_logger

logger = get_logger(__name__)


class DiversityMaximizingAssigner:
    """Assign lenses to maximize viewpoint diversity.

    Uses round-robin assignment to ensure each participant gets a distinct
    lens when possible. When participants outnumber lenses, lenses wrap
    around. This encourages different agents to approach the problem
    from different angles in different meetings.
    """

    def assign(
        self,
        participant_ids: tuple[str, ...],
        available_lenses: tuple[str, ...],
    ) -> dict[str, str]:
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

        result: dict[str, str] = {}
        for idx, participant_id in enumerate(participant_ids):
            lens_idx = idx % len(available_lenses)
            assigned_lens = available_lenses[lens_idx]
            result[participant_id] = assigned_lens

        return result

"""Autonomy change strategy protocol (see Operations design page, D7)."""

from typing import Protocol, runtime_checkable

from synthorg.core.enums import AutonomyLevel, DowngradeReason  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class AutonomyChangeStrategy(Protocol):
    """Strategy for managing runtime autonomy level changes.

    Implementations control how promotion requests, automatic
    downgrades, and recovery requests are handled.
    """

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Request a promotion to a higher autonomy level.

        Args:
            agent_id: The agent requesting promotion.
            target: The desired autonomy level.

        Returns:
            ``True`` if the promotion is immediately granted,
            ``False`` if it requires human approval.
        """
        ...

    def auto_downgrade(
        self,
        agent_id: NotBlankStr,
        reason: DowngradeReason,
        current_level: AutonomyLevel | None = None,
    ) -> AutonomyLevel:
        """Automatically downgrade an agent's autonomy level.

        Args:
            agent_id: The agent to downgrade.
            reason: Why the downgrade is happening.
            current_level: The agent's current effective autonomy level.
                Used as ``original_level`` when no prior override exists.

        Returns:
            The new (lower) autonomy level.
        """
        ...

    def request_recovery(
        self,
        agent_id: NotBlankStr,
    ) -> bool:
        """Request recovery from a previous downgrade.

        Args:
            agent_id: The agent requesting recovery.

        Returns:
            ``True`` if recovery is immediately granted,
            ``False`` if it requires human approval.
        """
        ...

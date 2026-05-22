"""Scope check guard.

Rejects proposals whose altitude is not enabled in the
self-improvement configuration.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_PROPOSAL_GUARD_PASSED,
    META_PROPOSAL_GUARD_REJECTED,
)

if TYPE_CHECKING:
    from synthorg.meta.config import SelfImprovementConfig

logger = get_logger(__name__)


class ScopeCheckGuard:
    """Rejects proposals outside the declared altitude scope.

    Args:
        config: Self-improvement configuration.
    """

    def __init__(self, *, config: SelfImprovementConfig) -> None:
        self._config = config

    @property
    def name(self) -> NotBlankStr:
        """Guard name."""
        return NotBlankStr("scope_check")

    async def evaluate(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Check if the proposal's altitude is enabled.

        Args:
            proposal: The proposal to evaluate.

        Returns:
            Guard result with PASSED or REJECTED verdict.
        """
        allowed = self._is_altitude_enabled(proposal.altitude)
        if allowed:
            logger.debug(
                META_PROPOSAL_GUARD_PASSED,
                guard=self.name,
                proposal_id=str(proposal.id),
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.PASSED,
            )

        reason = (
            f"Altitude '{proposal.altitude}' is not enabled "
            f"in self-improvement configuration"
        )
        logger.info(
            META_PROPOSAL_GUARD_REJECTED,
            guard=self.name,
            proposal_id=str(proposal.id),
            reason=reason,
        )
        return GuardResult(
            guard_name=self.name,
            verdict=GuardVerdict.REJECTED,
            reason=reason,
        )

    def _is_altitude_enabled(self, altitude: ProposalAltitude) -> bool:
        """Check if an altitude is enabled in config."""
        match altitude:
            case ProposalAltitude.CONFIG_TUNING:
                return self._config.config_tuning_enabled
            case ProposalAltitude.ARCHITECTURE:
                return self._config.architecture_proposals_enabled
            case ProposalAltitude.PROMPT_TUNING:
                return self._config.prompt_tuning_enabled
            case ProposalAltitude.CODE_MODIFICATION:
                return self._config.code_modification_enabled
            case ProposalAltitude.TOOL_CREATION:
                return self._config.tool_creation_enabled
            case _:
                return False  # type: ignore[unreachable]

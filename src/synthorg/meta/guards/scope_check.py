"""Scope check guard.

Rejects proposals whose altitude is not enabled in the
self-improvement configuration.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta._proposal_altitude_descriptor import (
    PROPOSAL_ALTITUDE_DESCRIPTORS,
)
from synthorg.meta.config import SelfImprovementConfig
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
        """Guard name.

        Returns:
            ``NotBlankStr`` instance.
        """
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
        """Check if an altitude is enabled in config.

        Reads the gating flag named by the altitude's descriptor, which
        is exhaustive over :class:`ProposalAltitude` by an import-time
        completeness guard.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        attr = PROPOSAL_ALTITUDE_DESCRIPTORS[altitude].enable_config_attr
        return bool(getattr(self._config, attr))

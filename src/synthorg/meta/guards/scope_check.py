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
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_bool_with_fallback
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)


class ScopeCheckGuard:
    """Rejects proposals outside the declared altitude scope.

    The altitude-enable flags are read live (with the baked config as the
    fallback) so a strategy toggle flipped at runtime is honoured by the
    guard on the next proposal, matching the per-cycle strategy filter. The
    restart-bound ``code_modification`` capability is not loosened by this:
    its strategy and applier are only built when baked-enabled, so a live
    guard pass has no strategy to generate a proposal nor an applier to apply
    one.

    Args:
        config: Self-improvement configuration (the baked fallback).
        config_resolver: Optional resolver for the live altitude-enable read.
    """

    def __init__(
        self,
        *,
        config: SelfImprovementConfig,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._config = config
        self._config_resolver = config_resolver

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
        allowed = await self._is_altitude_enabled(proposal.altitude)
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

    async def _is_altitude_enabled(self, altitude: ProposalAltitude) -> bool:
        """Check if an altitude is enabled, reading the flag live.

        Reads the gating flag named by the altitude's descriptor (exhaustive
        over :class:`ProposalAltitude` by an import-time completeness guard)
        from the ``self_improvement`` namespace, falling back to the baked
        config when no resolver is wired or the lookup fails. The attribute
        name matches the setting key, so the same descriptor drives both.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        attr = PROPOSAL_ALTITUDE_DESCRIPTORS[altitude].enable_config_attr
        return await resolve_bool_with_fallback(
            resolver=self._config_resolver,
            namespace=SettingNamespace.SELF_IMPROVEMENT,
            key=attr,
            fallback=bool(getattr(self._config, attr)),
        )

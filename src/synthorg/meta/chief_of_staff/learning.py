"""Confidence adjustment strategies for proposal learning.

Two pluggable strategies that blend a proposal's base confidence
with historical approval rates from the outcome store:

- **EMA**: Exponential moving average blend.
- **Bayesian**: Beta-conjugate posterior blend.
"""

from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.protocol import OutcomeStore
from synthorg.meta.models import ImprovementProposal
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import (
    COS_CONFIDENCE_ADJUSTED,
    COS_CONFIDENCE_NO_HISTORY,
)

logger = get_logger(__name__)

_DEFAULT_EMA_ALPHA: Final[float] = 0.5
_DEFAULT_BAYESIAN_PRIOR_ALPHA: Final[float] = 2.0
_DEFAULT_BAYESIAN_PRIOR_BETA: Final[float] = 2.0
_DEFAULT_BAYESIAN_BLEND: Final[float] = 0.7


class ExponentialMovingAverageAdjuster:
    """Confidence adjuster using exponential moving average.

    Blends the proposal's base confidence with the historical
    approval rate for the same rule/altitude:

    ``adjusted = alpha * base + (1 - alpha) * approval_rate``

    - ``alpha = 1.0``: ignore history (use base confidence).
    - ``alpha = 0.0``: ignore base (use historical rate).
    - ``alpha = 0.5`` (default): equal blend.

    Args:
        alpha: Blend factor between base confidence and history.
    """

    def __init__(self, *, alpha: float = _DEFAULT_EMA_ALPHA) -> None:
        self._alpha = alpha

    @property
    def name(self) -> NotBlankStr:
        """Strategy identifier.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("ema")

    async def adjust(
        self,
        proposal: ImprovementProposal,
        store: OutcomeStore,
    ) -> ImprovementProposal:
        """Adjust proposal confidence via EMA blend.

        Returns the proposal unchanged when ``source_rule`` is
        ``None`` or no historical stats are available.

        Args:
            proposal: Proposal to adjust.
            store: Outcome store for historical stats.

        Returns:
            Proposal with (possibly) adjusted confidence.
        """
        if proposal.source_rule is None:
            return proposal
        stats = await store.get_stats(proposal.source_rule, proposal.altitude)
        if stats is None:
            logger.debug(
                COS_CONFIDENCE_NO_HISTORY,
                proposal_id=str(proposal.id),
                rule=proposal.source_rule,
            )
            return proposal
        base = proposal.confidence
        adjusted = self._alpha * base + (1.0 - self._alpha) * stats.approval_rate
        adjusted = max(0.0, min(1.0, adjusted))
        logger.info(
            COS_CONFIDENCE_ADJUSTED,
            proposal_id=str(proposal.id),
            strategy="ema",
            original=base,
            adjusted=adjusted,
            approval_rate=stats.approval_rate,
            alpha=self._alpha,
        )
        return proposal.model_copy(update={"confidence": adjusted})


class BayesianConfidenceAdjuster:
    """Confidence adjuster using Beta-conjugate posterior.

    Models the approval rate as a Beta distribution:

    - Prior: ``Beta(prior_alpha, prior_beta)`` (default ``Beta(2, 2)``,
      centered at 0.5).
    - Posterior mean: ``(prior_alpha + approved) / (prior_alpha +
      prior_beta + total)``.
    - Blend: ``blend * base + (1 - blend) * posterior_mean``.

    Small samples are regularized toward 0.5 via the prior,
    preventing over-correction on sparse data.

    Args:
        prior_alpha: Beta prior alpha parameter.
        prior_beta: Beta prior beta parameter.
        blend: Weight for base confidence vs posterior.
    """

    def __init__(
        self,
        *,
        prior_alpha: float = _DEFAULT_BAYESIAN_PRIOR_ALPHA,
        prior_beta: float = _DEFAULT_BAYESIAN_PRIOR_BETA,
        blend: float = _DEFAULT_BAYESIAN_BLEND,
    ) -> None:
        self._prior_alpha = prior_alpha
        self._prior_beta = prior_beta
        self._blend = blend

    @property
    def name(self) -> NotBlankStr:
        """Strategy identifier.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("bayesian")

    async def adjust(
        self,
        proposal: ImprovementProposal,
        store: OutcomeStore,
    ) -> ImprovementProposal:
        """Adjust proposal confidence via Bayesian posterior.

        Returns the proposal unchanged when ``source_rule`` is
        ``None`` or no historical stats are available.

        Args:
            proposal: Proposal to adjust.
            store: Outcome store for historical stats.

        Returns:
            Proposal with (possibly) adjusted confidence.
        """
        if proposal.source_rule is None:
            return proposal
        stats = await store.get_stats(proposal.source_rule, proposal.altitude)
        if stats is None:
            logger.debug(
                COS_CONFIDENCE_NO_HISTORY,
                proposal_id=str(proposal.id),
                rule=proposal.source_rule,
            )
            return proposal
        posterior_mean = (self._prior_alpha + stats.approved_count) / (
            self._prior_alpha + self._prior_beta + stats.total_proposals
        )
        base = proposal.confidence
        adjusted = self._blend * base + (1.0 - self._blend) * posterior_mean
        adjusted = max(0.0, min(1.0, adjusted))
        logger.info(
            COS_CONFIDENCE_ADJUSTED,
            proposal_id=str(proposal.id),
            strategy="bayesian",
            original=base,
            adjusted=adjusted,
            posterior_mean=posterior_mean,
            blend=self._blend,
        )
        return proposal.model_copy(update={"confidence": adjusted})

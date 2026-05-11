"""RateLimitGuard -- caps adaptations per agent per day."""

import asyncio
from datetime import datetime, timedelta
from typing import Final

from synthorg.engine.evolution.models import AdaptationDecision, AdaptationProposal
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import EVOLUTION_RATE_LIMITED

logger = get_logger(__name__)
_DEFAULT_MAX_PER_DAY: Final[int] = 3


class RateLimitGuard:
    """Enforces a maximum number of adaptations per agent per day.

    Tracks adaptation timestamps per agent and rejects proposals that
    exceed the configured daily limit. Old timestamps are automatically
    cleaned up.
    """

    def __init__(self, max_per_day: int = _DEFAULT_MAX_PER_DAY) -> None:
        """Initialize RateLimitGuard.

        Args:
            max_per_day: Maximum adaptations allowed per agent per day.
        """
        self._max_per_day = max(1, max_per_day)
        self._timestamps: dict[str, list[datetime]] = {}
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        """Return guard name."""
        return "RateLimitGuard"

    async def evaluate(
        self,
        proposal: AdaptationProposal,
    ) -> AdaptationDecision:
        """Evaluate whether the proposal exceeds rate limits.

        Args:
            proposal: The adaptation proposal to evaluate.

        Returns:
            Approved decision if within limits, rejection otherwise.
        """
        agent_id = proposal.agent_id
        proposal_time = proposal.proposed_at
        cutoff = proposal_time - timedelta(days=1)

        async with self._lock:
            if agent_id not in self._timestamps:
                self._timestamps[agent_id] = []

            recent_timestamps = [ts for ts in self._timestamps[agent_id] if ts > cutoff]

            if len(recent_timestamps) >= self._max_per_day:
                logger.warning(
                    EVOLUTION_RATE_LIMITED,
                    agent_id=agent_id,
                    proposal_id=str(proposal.id),
                    count=len(recent_timestamps),
                    limit=self._max_per_day,
                )
                msg = (
                    f"Rate limit exceeded: {len(recent_timestamps)}/"
                    f"{self._max_per_day} adaptations in the last 24 hours"
                )
                return AdaptationDecision(
                    proposal_id=proposal.id,
                    approved=False,
                    guard_name=self.name,
                    reason=msg,
                )

            recent_timestamps.append(proposal_time)
            self._timestamps[agent_id] = recent_timestamps

        return AdaptationDecision(
            proposal_id=proposal.id,
            approved=True,
            guard_name=self.name,
            reason="Within daily rate limit",
        )

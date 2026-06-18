"""Rate limit guard.

Rejects proposals when the submission rate exceeds the configured
limit within the configured window.
"""

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_PROPOSAL_GUARD_PASSED,
    META_PROPOSAL_GUARD_REJECTED,
)

logger = get_logger(__name__)
_DEFAULT_MAX_PROPOSALS: Final[int] = 10
_DEFAULT_WINDOW_HOURS: Final[int] = 24
_SECONDS_PER_HOUR: Final[float] = 3600.0


class RateLimitGuard:
    """Rejects proposals when rate limit is exceeded.

    Tracks proposal submission timestamps in a sliding window.

    Args:
        max_proposals: Max proposals allowed per window.
        window_hours: Duration of the sliding window.
    """

    def __init__(
        self,
        *,
        max_proposals: int = _DEFAULT_MAX_PROPOSALS,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
    ) -> None:
        self._max_proposals = max_proposals
        self._window = timedelta(hours=window_hours)
        self._timestamps: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    @property
    def name(self) -> NotBlankStr:
        """Guard name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("rate_limit")

    async def evaluate(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Check if the proposal rate limit is exceeded.

        Args:
            proposal: The proposal to evaluate.

        Returns:
            Guard result with PASSED or REJECTED verdict.
        """
        async with self._lock:
            return await self._evaluate_locked(proposal)

    async def _evaluate_locked(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Rate limit check under lock (no concurrent mutation).

        Returns:
            ``GuardResult`` instance.
        """
        now = datetime.now(UTC)
        cutoff = now - self._window

        # Evict old timestamps.
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._max_proposals:
            reason = (
                f"Rate limit exceeded: {len(self._timestamps)} "
                f"proposals in last "
                f"{self._window.total_seconds() / _SECONDS_PER_HOUR:.0f}h "
                f"(max {self._max_proposals})"
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

        self._timestamps.append(now)
        logger.debug(
            META_PROPOSAL_GUARD_PASSED,
            guard=self.name,
            proposal_id=str(proposal.id),
            count=len(self._timestamps),
        )
        return GuardResult(
            guard_name=self.name,
            verdict=GuardVerdict.PASSED,
        )

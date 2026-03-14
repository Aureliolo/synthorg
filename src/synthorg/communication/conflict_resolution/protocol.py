"""Conflict resolution protocol interfaces (see Communication design page).

Defines the pluggable strategy interface that varies per resolution
approach (``resolve`` + ``build_dissent_records``).  Detection logic
lives on the service, not the protocol, because it is strategy-agnostic.
"""

from typing import NamedTuple, Protocol

from synthorg.communication.conflict_resolution.models import (  # noqa: TC001
    Conflict,
    ConflictResolution,
    DissentRecord,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


class JudgeDecision(NamedTuple):
    """Result of an LLM-based judge evaluation.

    Attributes:
        winning_agent_id: Agent whose position was chosen.
        reasoning: Explanation for the decision.
    """

    winning_agent_id: str
    reasoning: str


class ConflictResolver(Protocol):
    """Protocol for conflict resolution strategies.

    Each strategy implements ``resolve`` (async, may need LLM calls)
    and ``build_dissent_records`` (sync, builds audit artifacts for
    every overruled position).
    """

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Resolve a conflict and produce a decision.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Resolution decision.
        """
        ...

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build audit records for all losing positions.

        For N-party conflicts, produces one ``DissentRecord`` per
        overruled agent.  For escalated conflicts, produces one
        record per position (all are pending human review).

        Args:
            conflict: The original conflict.
            resolution: The resolution decision.

        Returns:
            Dissent records preserving every overruled reasoning.
        """
        ...


class JudgeEvaluator(Protocol):
    """Protocol for LLM-based judge evaluation.

    Used by debate and hybrid strategies.  When absent, strategies
    fall back to authority-based judging.
    """

    async def evaluate(
        self,
        conflict: Conflict,
        judge_agent_id: NotBlankStr,
    ) -> JudgeDecision:
        """Evaluate conflict positions and pick a winner.

        Args:
            conflict: The conflict with agent positions.
            judge_agent_id: The agent acting as judge.

        Returns:
            Decision containing the winning agent ID and reasoning.
        """
        ...

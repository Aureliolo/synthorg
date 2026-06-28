"""Pluggable strategies for eval-loop pattern identification and fix proposal.

``EvalLoopCoordinator`` delegates the IDENTIFY and PROPOSE steps to these
protocols so a deployment can swap the shipped deterministic strategies
(threshold counting + a static action table) for richer
provider-backed analysis without touching the orchestrator. Both
strategies speak the established token vocabulary: weakness patterns
(``"weakness:<pillar>"``) and free-form action identifiers.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr
from synthorg.hr.evaluation.models import EvaluationReport


@runtime_checkable
class PatternIdentifier(Protocol):
    """Identifies cross-agent weakness patterns from evaluation reports."""

    async def identify(
        self,
        reports: tuple[EvaluationReport, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Identify patterns across the cycle's per-agent reports.

        Args:
            reports: Per-agent evaluation reports from the current cycle.

        Returns:
            Pattern tokens (e.g. ``"weakness:governance"``).
        """
        ...


@runtime_checkable
class FixProposer(Protocol):
    """Proposes remediation action identifiers for identified patterns."""

    async def propose(
        self,
        patterns: tuple[NotBlankStr, ...],
    ) -> tuple[NotBlankStr, ...]:
        """Map identified patterns to remediation action identifiers.

        Args:
            patterns: Pattern tokens from a :class:`PatternIdentifier`.

        Returns:
            Ordered, de-duplicated action identifiers.
        """
        ...

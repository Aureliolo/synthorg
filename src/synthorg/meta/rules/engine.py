"""Rule engine for evaluating signal patterns.

Runs all enabled rules against an OrgSignalSnapshot and returns
matched rules sorted by severity (critical first).
"""

from typing import TYPE_CHECKING

from synthorg.meta.models import (
    OrgSignalSnapshot,
    RuleMatch,
    RuleSeverity,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_RULE_EVALUATED,
    META_RULE_EVALUATION_FAILED,
    META_RULE_FIRED,
)

if TYPE_CHECKING:
    from synthorg.meta.protocol import SignalRule

logger = get_logger(__name__)

_SEVERITY_ORDER = {
    RuleSeverity.CRITICAL: 0,
    RuleSeverity.WARNING: 1,
    RuleSeverity.INFO: 2,
}


class RuleEngine:
    """Evaluates signal rules against org snapshots.

    Args:
        rules: Tuple of enabled signal rules.
    """

    def __init__(self, *, rules: tuple[SignalRule, ...]) -> None:
        self._rules = rules

    @property
    def rule_count(self) -> int:
        """Number of registered rules."""
        return len(self._rules)

    @property
    def rule_names(self) -> tuple[str, ...]:
        """Names of all registered rules."""
        return tuple(r.name for r in self._rules)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> tuple[RuleMatch, ...]:
        """Evaluate all rules against the snapshot.

        Args:
            snapshot: Current org-wide signal snapshot.

        Returns:
            Matched rules sorted by severity (critical first).
            Empty tuple if no rules fire.
        """
        matches: list[RuleMatch] = []

        for rule in self._rules:
            try:
                match = rule.evaluate(snapshot)
                logger.debug(
                    META_RULE_EVALUATED,
                    rule=rule.name,
                    matched=match is not None,
                )
                if match is not None:
                    logger.info(
                        META_RULE_FIRED,
                        rule=rule.name,
                        severity=match.severity.value,
                        description=match.description,
                    )
                    matches.append(match)
            except Exception as exc:
                logger.error(
                    META_RULE_EVALUATION_FAILED,
                    rule=rule.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        return tuple(
            sorted(
                matches,
                key=lambda m: _SEVERITY_ORDER.get(m.severity, 99),
            )
        )

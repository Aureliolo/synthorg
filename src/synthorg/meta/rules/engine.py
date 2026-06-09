"""Rule engine for evaluating signal patterns.

Runs all enabled rules against an OrgSignalSnapshot and returns
matched rules sorted by severity (critical first).
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.meta.models import (
    OrgSignalSnapshot,
    RuleMatch,
    RuleSeverity,
)
from synthorg.meta.protocol import SignalRule
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_RULE_EVALUATED,
    META_RULE_EVALUATION_FAILED,
    META_RULE_FIRED,
)

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
        """Number of registered rules.

        Returns:
            Resulting integer.
        """
        return len(self._rules)

    @property
    def rule_names(self) -> tuple[str, ...]:
        """Names of all registered rules.

        Returns:
            Tuple of the declared element types.
        """
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
                reraise_critical(exc)
                log_exception_redacted(
                    logger, META_RULE_EVALUATION_FAILED, exc, rule=rule.name
                )

        return tuple(
            sorted(
                matches,
                key=lambda m: _SEVERITY_ORDER.get(m.severity, 99),
            )
        )

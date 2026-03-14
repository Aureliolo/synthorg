"""Rule engine — evaluates security rules in order."""

import time
from datetime import UTC, datetime

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_EVALUATE_COMPLETE,
    SECURITY_RULE_ERROR,
    SECURITY_RULE_MATCHED,
    SECURITY_VERDICT_ALLOW,
)
from synthorg.security.config import RuleEngineConfig  # noqa: TC001
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules.policy_validator import (
    _RULE_NAME as _POLICY_VALIDATOR_RULE_NAME,
)
from synthorg.security.rules.protocol import SecurityRule  # noqa: TC001
from synthorg.security.rules.risk_classifier import RiskClassifier  # noqa: TC001

logger = get_logger(__name__)

# Rules whose ALLOW verdict should not short-circuit remaining rules.
_SOFT_ALLOW_RULES: frozenset[str] = frozenset({_POLICY_VALIDATOR_RULE_NAME})


class RuleEngine:
    """Evaluates security rules in a defined order.

    Rules are run sequentially.  The first DENY or ESCALATE verdict
    wins.  If no rule triggers, the engine returns ALLOW with a risk
    level from the ``RiskClassifier``.

    The evaluation order is determined solely by the ``rules`` tuple
    passed at construction.  The recommended (but not enforced) order is:
        1. Policy validator (fast path: hard deny / auto approve)
        2. Credential detector
        3. Path traversal detector
        4. Destructive operation detector
        5. Data leak detector

    An ALLOW from the policy validator (auto-approve) does NOT
    short-circuit remaining detection rules.  Only DENY/ESCALATE
    from the policy validator is a hard exit.  This ensures that
    auto-approved action types are still scanned for credentials,
    path traversal, etc.

    All rules are synchronous — the engine itself is synchronous.
    """

    def __init__(
        self,
        *,
        rules: tuple[SecurityRule, ...],
        risk_classifier: RiskClassifier,
        config: RuleEngineConfig,
    ) -> None:
        """Initialize the rule engine.

        Args:
            rules: Ordered tuple of rules to evaluate.
            risk_classifier: Fallback risk classifier.
            config: Rule engine configuration.
        """
        self._rules = rules
        self._risk_classifier = risk_classifier
        self._config = config

    def evaluate(self, context: SecurityContext) -> SecurityVerdict:
        """Run all rules in order, returning the final verdict.

        Individual rule failures are caught and logged.  A failing
        rule results in DENY (fail-closed) for that rule.

        Args:
            context: The tool invocation security context.

        Returns:
            A ``SecurityVerdict`` — DENY/ESCALATE from the first
            matching rule, or ALLOW with risk from the classifier.
        """
        start = time.monotonic()
        soft_allow: SecurityVerdict | None = None

        for rule in self._rules:
            verdict = self._safe_evaluate(rule, context)
            if verdict is None:
                continue

            duration_ms = (time.monotonic() - start) * 1000

            # Soft-allow rules (e.g. policy_validator auto-approve)
            # record their verdict but do NOT short-circuit.
            if (
                verdict.verdict == SecurityVerdictType.ALLOW
                and rule.name in _SOFT_ALLOW_RULES
            ):
                soft_allow = verdict.model_copy(
                    update={"evaluation_duration_ms": duration_ms},
                )
                continue

            # DENY / ESCALATE / hard ALLOW → return immediately.
            logger.debug(
                SECURITY_RULE_MATCHED,
                rule_name=rule.name,
                verdict=verdict.verdict.value,
                tool_name=context.tool_name,
            )
            return verdict.model_copy(
                update={"evaluation_duration_ms": duration_ms},
            )

        # No rule returned DENY/ESCALATE.
        duration_ms = (time.monotonic() - start) * 1000

        # If a soft-allow was recorded, use it.
        if soft_allow is not None:
            logger.debug(
                SECURITY_EVALUATE_COMPLETE,
                tool_name=context.tool_name,
                duration_ms=duration_ms,
            )
            return soft_allow.model_copy(
                update={"evaluation_duration_ms": duration_ms},
            )

        # Fallback: ALLOW with risk from classifier.
        risk = self._risk_classifier.classify(context.action_type)
        logger.debug(
            SECURITY_VERDICT_ALLOW,
            tool_name=context.tool_name,
            risk_level=risk.value,
        )
        logger.debug(
            SECURITY_EVALUATE_COMPLETE,
            tool_name=context.tool_name,
            duration_ms=duration_ms,
        )
        return SecurityVerdict(
            verdict=SecurityVerdictType.ALLOW,
            reason="No security rule triggered",
            risk_level=risk,
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=duration_ms,
        )

    def _safe_evaluate(
        self,
        rule: SecurityRule,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Evaluate a single rule, catching exceptions (fail-closed)."""
        try:
            return rule.evaluate(context)
        except Exception:
            logger.exception(
                SECURITY_RULE_ERROR,
                rule_name=rule.name,
                tool_name=context.tool_name,
            )
            return SecurityVerdict(
                verdict=SecurityVerdictType.DENY,
                reason=f"Security rule {rule.name!r} failed (fail-closed)",
                risk_level=ApprovalRiskLevel.CRITICAL,
                matched_rules=(rule.name,),
                evaluated_at=datetime.now(UTC),
                evaluation_duration_ms=0.0,
            )

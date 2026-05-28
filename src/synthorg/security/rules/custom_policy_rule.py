"""Custom policy rule -- evaluates user-defined security policies."""

from datetime import UTC, datetime
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_CUSTOM_POLICY_MATCHED
from synthorg.security.config import SecurityPolicyRule
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
)

logger = get_logger(__name__)

_RULE_NAME_PREFIX: Final[str] = "custom_policy:"


class CustomPolicyRule:
    """Evaluates a user-defined ``SecurityPolicyRule`` against tool contexts.

    Each instance wraps a single ``SecurityPolicyRule`` from the config.
    The rule matches when the context's ``action_type`` appears in the
    policy's ``action_types`` tuple and the policy is enabled.

    By default, custom policy rules are placed after all built-in
    detectors in the evaluation pipeline, ensuring that credential,
    path-traversal, and other security detectors always run first.
    When ``RuleEngineConfig.custom_allow_bypasses_detectors`` is
    ``True``, rules are placed before detectors instead -- a custom
    ALLOW can then short-circuit security scanning for matched
    action types.

    Attributes:
        name: Rule name, prefixed with ``custom_policy:`` to
            distinguish from built-in rules.
        policy: The wrapped ``SecurityPolicyRule`` config.
    """

    def __init__(self, policy: SecurityPolicyRule) -> None:
        """Initialize with a security policy rule config.

        Args:
            policy: The user-defined policy rule to evaluate.
        """
        self._policy = policy
        self._action_types = frozenset(policy.action_types)

    @property
    def name(self) -> str:
        """Rule name, prefixed to distinguish from built-in rules."""
        return f"{_RULE_NAME_PREFIX}{self._policy.name}"

    @property
    def policy(self) -> SecurityPolicyRule:
        """The wrapped policy config."""
        return self._policy

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Evaluate the custom policy against a security context.

        Returns a verdict when the policy is enabled and the context's
        action_type matches one of the policy's action_types.  Returns
        ``None`` otherwise (pass-through).

        Args:
            context: The tool invocation security context.

        Returns:
            A verdict matching the policy's configured verdict and
            risk_level, or ``None`` if the rule does not apply.
        """
        if not self._policy.enabled:
            return None

        if context.action_type not in self._action_types:
            return None

        reason = (
            f"Custom policy {self._policy.name!r} matched "
            f"action_type {context.action_type!r}"
        )
        if self._policy.description:
            reason = f"{reason} -- {self._policy.description}"

        logger.info(
            SECURITY_CUSTOM_POLICY_MATCHED,
            policy_name=self._policy.name,
            action_type=context.action_type,
            verdict=self._policy.verdict.value,
            tool_name=context.tool_name,
        )
        return SecurityVerdict(
            verdict=self._policy.verdict,
            reason=reason,
            risk_level=self._policy.risk_level,
            matched_rules=(self.name,),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,  # overwritten by RuleEngine after timing
        )

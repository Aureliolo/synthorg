"""Timeout policy implementations — wait, deny, tiered, escalation chain."""

from ai_company.core.approval import ApprovalItem  # noqa: TC001
from ai_company.core.enums import TimeoutActionType
from ai_company.observability import get_logger
from ai_company.observability.events.timeout import (
    TIMEOUT_AUTO_DENIED,
    TIMEOUT_ESCALATED,
    TIMEOUT_POLICY_EVALUATED,
    TIMEOUT_WAITING,
)
from ai_company.security.timeout.config import (
    EscalationStep,  # noqa: TC001
    TierConfig,  # noqa: TC001
)
from ai_company.security.timeout.models import TimeoutAction
from ai_company.security.timeout.protocol import RiskTierClassifier  # noqa: TC001

logger = get_logger(__name__)

_SECONDS_PER_MINUTE = 60.0


class WaitForeverPolicy:
    """Always returns WAIT — no automatic timeout action.

    This is the safest default: approvals remain pending until a
    human responds.
    """

    async def determine_action(
        self,
        item: ApprovalItem,
        elapsed_seconds: float,
    ) -> TimeoutAction:
        """Always wait.

        Args:
            item: The pending approval item.
            elapsed_seconds: Seconds since creation.

        Returns:
            WAIT action.
        """
        logger.debug(
            TIMEOUT_WAITING,
            approval_id=item.id,
            elapsed_seconds=elapsed_seconds,
        )
        return TimeoutAction(
            action=TimeoutActionType.WAIT,
            reason="Wait-forever policy — no automatic action",
        )


class DenyOnTimeoutPolicy:
    """Deny the action after a fixed timeout.

    Args:
        timeout_seconds: Seconds before auto-deny.
    """

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    async def determine_action(
        self,
        item: ApprovalItem,
        elapsed_seconds: float,
    ) -> TimeoutAction:
        """WAIT if under timeout, DENY if over.

        Args:
            item: The pending approval item.
            elapsed_seconds: Seconds since creation.

        Returns:
            WAIT or DENY action.
        """
        if elapsed_seconds < self._timeout_seconds:
            logger.debug(
                TIMEOUT_WAITING,
                approval_id=item.id,
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=self._timeout_seconds,
            )
            return TimeoutAction(
                action=TimeoutActionType.WAIT,
                reason=(
                    f"Waiting — {elapsed_seconds:.0f}s of "
                    f"{self._timeout_seconds:.0f}s elapsed"
                ),
            )

        logger.info(
            TIMEOUT_AUTO_DENIED,
            approval_id=item.id,
            elapsed_seconds=elapsed_seconds,
            timeout_seconds=self._timeout_seconds,
        )
        return TimeoutAction(
            action=TimeoutActionType.DENY,
            reason=(
                f"Auto-denied after {elapsed_seconds:.0f}s "
                f"(timeout: {self._timeout_seconds:.0f}s)"
            ),
        )


class TieredTimeoutPolicy:
    """Per-risk-tier timeout with configurable actions.

    Uses a :class:`RiskTierClassifier` to determine the risk tier
    of each approval item, then applies the corresponding tier
    configuration.

    Args:
        tiers: Tier configurations keyed by risk level name.
        classifier: Risk tier classifier for action types.
    """

    def __init__(
        self,
        *,
        tiers: dict[str, TierConfig],
        classifier: RiskTierClassifier,
    ) -> None:
        self._tiers = tiers
        self._classifier = classifier

    async def determine_action(
        self,
        item: ApprovalItem,
        elapsed_seconds: float,
    ) -> TimeoutAction:
        """Apply the tier-specific timeout policy.

        Args:
            item: The pending approval item.
            elapsed_seconds: Seconds since creation.

        Returns:
            WAIT, DENY, APPROVE, or ESCALATE based on tier config.
        """
        risk_level = self._classifier.classify(item.action_type)
        tier_config = self._tiers.get(risk_level.value)

        if tier_config is None:
            # No tier configured for this risk level — wait (safe default).
            logger.debug(
                TIMEOUT_WAITING,
                approval_id=item.id,
                risk_level=risk_level.value,
                note="no tier config — defaulting to wait",
            )
            return TimeoutAction(
                action=TimeoutActionType.WAIT,
                reason=(
                    f"No tier config for risk level {risk_level.value!r} — waiting"
                ),
            )

        timeout_seconds = tier_config.timeout_minutes * _SECONDS_PER_MINUTE

        if elapsed_seconds < timeout_seconds:
            logger.debug(
                TIMEOUT_WAITING,
                approval_id=item.id,
                risk_level=risk_level.value,
                elapsed_seconds=elapsed_seconds,
                timeout_seconds=timeout_seconds,
            )
            return TimeoutAction(
                action=TimeoutActionType.WAIT,
                reason=(
                    f"Tier {risk_level.value}: {elapsed_seconds:.0f}s of "
                    f"{timeout_seconds:.0f}s elapsed"
                ),
            )

        logger.info(
            TIMEOUT_POLICY_EVALUATED,
            approval_id=item.id,
            risk_level=risk_level.value,
            on_timeout=tier_config.on_timeout.value,
            elapsed_seconds=elapsed_seconds,
        )
        return TimeoutAction(
            action=tier_config.on_timeout,
            reason=(
                f"Tier {risk_level.value} timeout: auto-"
                f"{tier_config.on_timeout.value} after "
                f"{elapsed_seconds:.0f}s"
            ),
        )


class EscalationChainPolicy:
    """Escalate through a chain of roles, each with its own timeout.

    When the entire chain is exhausted, applies the
    ``on_chain_exhausted`` action.

    Args:
        chain: Ordered escalation steps.
        on_chain_exhausted: Action when all steps exhaust.
    """

    def __init__(
        self,
        *,
        chain: tuple[EscalationStep, ...],
        on_chain_exhausted: TimeoutActionType,
    ) -> None:
        self._chain = chain
        self._on_chain_exhausted = on_chain_exhausted

    async def determine_action(
        self,
        item: ApprovalItem,
        elapsed_seconds: float,
    ) -> TimeoutAction:
        """Determine the current escalation step.

        Calculates cumulative timeouts to find which step the
        approval is currently at.

        Args:
            item: The pending approval item.
            elapsed_seconds: Seconds since creation.

        Returns:
            WAIT, ESCALATE, or the chain-exhausted action.
        """
        if not self._chain:
            return TimeoutAction(
                action=self._on_chain_exhausted,
                reason="Empty escalation chain — applying exhausted action",
            )

        cumulative_seconds = 0.0
        for step in self._chain:
            step_timeout = step.timeout_minutes * _SECONDS_PER_MINUTE
            if elapsed_seconds < cumulative_seconds + step_timeout:
                # Still within this step's window.
                if elapsed_seconds < cumulative_seconds:
                    # Before this step — shouldn't happen but safe.
                    break
                logger.debug(
                    TIMEOUT_WAITING,
                    approval_id=item.id,
                    escalation_role=step.role,
                    elapsed_seconds=elapsed_seconds,
                )
                return TimeoutAction(
                    action=TimeoutActionType.ESCALATE,
                    reason=(
                        f"Escalated to {step.role!r} — {elapsed_seconds:.0f}s elapsed"
                    ),
                    escalate_to=step.role,
                )
            cumulative_seconds += step_timeout

        # All steps exhausted.
        logger.info(
            TIMEOUT_ESCALATED,
            approval_id=item.id,
            elapsed_seconds=elapsed_seconds,
            on_exhausted=self._on_chain_exhausted.value,
            note="escalation chain exhausted",
        )
        return TimeoutAction(
            action=self._on_chain_exhausted,
            reason=(
                f"Escalation chain exhausted after {elapsed_seconds:.0f}s "
                f"— {self._on_chain_exhausted.value}"
            ),
        )

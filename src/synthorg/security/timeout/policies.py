"""Timeout policy implementations — wait, deny, tiered, escalation chain."""

from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.enums import ApprovalRiskLevel, TimeoutActionType
from synthorg.observability import get_logger
from synthorg.observability.events.timeout import (
    TIMEOUT_AUTO_DENIED,
    TIMEOUT_ESCALATED,
    TIMEOUT_POLICY_EVALUATED,
    TIMEOUT_WAITING,
)
from synthorg.security.timeout.config import (
    EscalationStep,  # noqa: TC001
    TierConfig,  # noqa: TC001
)
from synthorg.security.timeout.models import TimeoutAction
from synthorg.security.timeout.protocol import RiskTierClassifier  # noqa: TC001

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
        if timeout_seconds <= 0:
            msg = f"timeout_seconds must be positive, got {timeout_seconds}"
            raise ValueError(msg)
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
        # Default: classify by risk level, then check explicit tier overrides.
        risk_level = self._classifier.classify(item.action_type)
        tier_config = None
        for tier_key, cfg in self._tiers.items():
            if cfg.actions and item.action_type in cfg.actions:
                tier_config = cfg
                risk_level = ApprovalRiskLevel(tier_key)
                break

        # Fall back to risk-level-based tier lookup.
        if tier_config is None:
            tier_config = self._tiers.get(risk_level.value)

        if tier_config is None:
            # No tier configured for this risk level — wait (safe default).
            logger.warning(
                TIMEOUT_WAITING,
                approval_id=item.id,
                risk_level=risk_level.value,
                available_tiers=sorted(self._tiers.keys()),
                note="no tier config for this risk level — defaulting to wait",
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

        effective_action = tier_config.on_timeout

        # Guard: never auto-approve HIGH or CRITICAL actions.
        _approve_forbidden = {ApprovalRiskLevel.HIGH, ApprovalRiskLevel.CRITICAL}
        if (
            effective_action == TimeoutActionType.APPROVE
            and risk_level in _approve_forbidden
        ):
            logger.warning(
                TIMEOUT_POLICY_EVALUATED,
                approval_id=item.id,
                risk_level=risk_level.value,
                configured_action=effective_action.value,
                note=(
                    "auto-approve blocked for high/critical risk — overriding to DENY"
                ),
            )
            effective_action = TimeoutActionType.DENY

        logger.info(
            TIMEOUT_POLICY_EVALUATED,
            approval_id=item.id,
            risk_level=risk_level.value,
            on_timeout=effective_action.value,
            elapsed_seconds=elapsed_seconds,
        )
        return TimeoutAction(
            action=effective_action,
            reason=(
                f"Tier {risk_level.value} timeout: auto-"
                f"{effective_action.value} after "
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
            ESCALATE (to the current step's role) or the
            chain-exhausted action.
        """
        if not self._chain:
            logger.warning(
                TIMEOUT_ESCALATED,
                approval_id=item.id,
                on_exhausted=self._on_chain_exhausted.value,
                note="empty escalation chain — likely a configuration error",
            )
            return TimeoutAction(
                action=self._on_chain_exhausted,
                reason="Empty escalation chain — applying exhausted action",
            )

        cumulative_seconds = 0.0
        for idx, step in enumerate(self._chain):
            step_timeout = step.timeout_minutes * _SECONDS_PER_MINUTE
            step_end = cumulative_seconds + step_timeout
            if elapsed_seconds < step_end:
                if idx == 0:
                    # First step hasn't timed out yet — WAIT.
                    logger.debug(
                        TIMEOUT_WAITING,
                        approval_id=item.id,
                        escalation_role=step.role,
                        elapsed_seconds=elapsed_seconds,
                    )
                    return TimeoutAction(
                        action=TimeoutActionType.WAIT,
                        reason=(
                            f"Waiting at {step.role!r} — "
                            f"{elapsed_seconds:.0f}s of "
                            f"{step_end:.0f}s elapsed"
                        ),
                    )
                # Previous step timed out — escalate to this step's role.
                logger.info(
                    TIMEOUT_ESCALATED,
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

"""Cedar policy engine adapter using ``cedarpy``."""

import json
import time

import cedarpy

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_POLICY_DECISION_ALLOW,
    SECURITY_POLICY_DECISION_DENY,
    SECURITY_POLICY_ENGINE_ERROR,
    SECURITY_POLICY_EVALUATE_START,
)
from synthorg.observability.metrics_hub import record_security_verdict
from synthorg.security.policy_engine.models import (
    PolicyActionRequest,
    PolicyDecision,
)

logger = get_logger(__name__)


class CedarPolicyEngine:
    """Cedar-based runtime policy evaluator.

    Uses ``cedarpy.is_authorized()`` for stateless embedded policy
    evaluation.  Policies are loaded at construction time from text
    strings.

    Args:
        policy_texts: Cedar policy source strings.
        fail_closed: If ``True``, return deny on evaluation errors.
    """

    def __init__(
        self,
        policy_texts: tuple[str, ...],
        *,
        fail_closed: bool = False,
    ) -> None:
        self._policies = "\n".join(policy_texts)
        self._fail_closed = fail_closed

    @property
    def name(self) -> str:
        """Engine identifier."""
        return "cedar"

    async def evaluate(
        self,
        request: PolicyActionRequest,
    ) -> PolicyDecision:
        """Evaluate a policy action request using Cedar.

        Args:
            request: The action to evaluate.

        Returns:
            Allow/deny decision with reason and timing.
        """
        logger.debug(
            SECURITY_POLICY_EVALUATE_START,
            action_type=request.action_type,
            principal=request.principal,
            resource=request.resource,
        )

        # Use json.dumps for proper escaping of all special characters
        # in Cedar entity UIDs to prevent syntax injection.
        def _esc(v: object) -> str:
            return json.dumps(str(v), ensure_ascii=False)

        cedar_request = {
            "principal": f"Principal::{_esc(request.principal)}",
            "action": f"Action::{_esc(request.action_type)}",
            "resource": f"Resource::{_esc(request.resource)}",
            "context": dict(request.context),
        }

        start = time.perf_counter()
        decision: PolicyDecision | None = None
        try:
            result = cedarpy.is_authorized(
                cedar_request,
                self._policies,
                [],
            )
            latency_ms = (time.perf_counter() - start) * 1000

            allowed = result.decision == cedarpy.Decision.Allow
            reason = (
                "Cedar policy permits action"
                if allowed
                else "Cedar policy denies action"
            )

            event = (
                SECURITY_POLICY_DECISION_ALLOW
                if allowed
                else SECURITY_POLICY_DECISION_DENY
            )
            logger.info(
                event,
                action_type=request.action_type,
                principal=request.principal,
                resource=request.resource,
                allowed=allowed,
                latency_ms=latency_ms,
            )
            decision = PolicyDecision(
                allow=allowed,
                reason=reason,
                matched_policy="cedar_policy_set",
                latency_ms=latency_ms,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.error(
                SECURITY_POLICY_ENGINE_ERROR,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                action_type=request.action_type,
                principal=request.principal,
                resource=request.resource,
                fail_closed=self._fail_closed,
            )

            if self._fail_closed:
                decision = PolicyDecision(
                    allow=False,
                    reason=f"Policy evaluation error (fail-closed): {exc}",
                    latency_ms=latency_ms,
                )
            else:
                decision = PolicyDecision(
                    allow=True,
                    reason=f"Policy evaluation error (fail-open): {exc}",
                    latency_ms=latency_ms,
                )

        # Record the verdict in Prometheus *after* the authoritative
        # decision is built. Pulling the metrics hook out of the
        # Cedar-evaluation try/except prevents a hook exception from
        # being misinterpreted as a policy-engine failure (which
        # would flip ``allowed`` in fail-open mode). The try/except
        # here is defence-in-depth: ``metrics_hub`` already swallows
        # collector exceptions, but a bug in that layer must never
        # block a ready policy decision from being returned.
        try:
            record_security_verdict("allow" if decision.allow else "deny")
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                SECURITY_POLICY_ENGINE_ERROR,
                reason="metrics_mirror_failed",
                decision_allow=decision.allow,
                action_type=request.action_type,
            )
        return decision

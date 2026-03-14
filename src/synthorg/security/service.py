"""SecOps service — the security meta-agent.

Coordinates the rule engine, audit log, output scanner, output scan
response policy, and approval store into a single
``SecurityInterceptionStrategy`` implementation that the
``ToolInvoker`` calls.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.observability import get_logger
from synthorg.observability.events.autonomy import (
    AUTONOMY_ACTION_AUTO_APPROVED,
    AUTONOMY_ACTION_HUMAN_REQUIRED,
)
from synthorg.observability.events.security import (
    SECURITY_AUDIT_RECORD_ERROR,
    SECURITY_CONFIG_LOADED,
    SECURITY_DISABLED,
    SECURITY_ESCALATION_CREATED,
    SECURITY_ESCALATION_STORE_ERROR,
    SECURITY_EVALUATE_COMPLETE,
    SECURITY_EVALUATE_START,
    SECURITY_INTERCEPTOR_ERROR,
    SECURITY_VERDICT_ALLOW,
    SECURITY_VERDICT_DENY,
    SECURITY_VERDICT_ESCALATE,
)
from synthorg.security.audit import AuditLog  # noqa: TC001
from synthorg.security.autonomy.models import EffectiveAutonomy  # noqa: TC001
from synthorg.security.config import SecurityConfig  # noqa: TC001
from synthorg.security.models import (
    OUTPUT_SCAN_VERDICT,
    AuditEntry,
    OutputScanResult,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.output_scan_policy import (
    OutputScanResponsePolicy,  # noqa: TC001
)
from synthorg.security.output_scan_policy_factory import (
    build_output_scan_policy,
)
from synthorg.security.output_scanner import OutputScanner  # noqa: TC001
from synthorg.security.rules.engine import RuleEngine  # noqa: TC001
from synthorg.security.timeout.protocol import RiskTierClassifier  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.api.approval_store import ApprovalStore

logger = get_logger(__name__)


def _hash_arguments(arguments: dict[str, object]) -> str:
    """Produce a SHA-256 hex digest of serialized arguments.

    Uses ``default=str`` for non-JSON-serializable values.  This means
    two distinct objects with the same ``str()`` will produce identical
    hashes — acceptable for tool arguments (strings, ints, lists, dicts)
    but not a guaranteed-unique fingerprint for arbitrary types.
    """
    serialized = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


class SecOpsService:
    """Implements ``SecurityInterceptionStrategy``.

    Coordinates the rule engine, audit log, output scanner, output
    scan response policy, and optional approval store.  Enforces
    security policies, scans for sensitive data, and records audit
    entries.

    On ESCALATE: creates an ``ApprovalItem`` in the ``ApprovalStore``
    and returns the verdict with ``approval_id`` set.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: SecurityConfig,
        rule_engine: RuleEngine,
        audit_log: AuditLog,
        output_scanner: OutputScanner,
        approval_store: ApprovalStore | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
        risk_classifier: RiskTierClassifier | None = None,
        output_scan_policy: OutputScanResponsePolicy | None = None,
    ) -> None:
        """Initialize the SecOps service.

        Args:
            config: Security configuration.
            rule_engine: The synchronous rule engine.
            audit_log: Audit log for recording evaluations.
            output_scanner: Post-tool output scanner.
            approval_store: Optional store for escalation items.
            effective_autonomy: Resolved autonomy for the current run.
                When provided, autonomy routing is applied *after*
                the rule engine — never bypassing security detectors.
            risk_classifier: Optional classifier for determining action
                risk levels in autonomy escalations.  Defaults to HIGH
                when absent (fail-safe).
            output_scan_policy: Policy applied to scan results before
                returning.  When ``None``, a default policy is built
                from ``config.output_scan_policy_type`` via the
                factory.  Pass an explicit instance to override.
        """
        self._config = config
        self._rule_engine = rule_engine
        self._audit_log = audit_log
        self._output_scanner = output_scanner
        self._approval_store = approval_store
        self._effective_autonomy = effective_autonomy
        self._risk_classifier = risk_classifier
        self._output_scan_policy: OutputScanResponsePolicy = (
            output_scan_policy
            if output_scan_policy is not None
            else build_output_scan_policy(
                config.output_scan_policy_type,
                effective_autonomy=effective_autonomy,
            )
        )

        if config.custom_policies:
            logger.warning(
                SECURITY_CONFIG_LOADED,
                note=(
                    "custom_policies configured but not yet "
                    "evaluated — enforcement is not implemented"
                ),
                policy_count=len(config.custom_policies),
            )

    async def evaluate_pre_tool(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict:
        """Evaluate a tool invocation before execution.

        Steps:
            1. Run rule engine.
            2. If ESCALATE, create approval item (or convert to DENY).
            3. Record audit entry.
            4. Return verdict.
        """
        if not self._config.enabled:
            logger.warning(SECURITY_DISABLED, tool_name=context.tool_name)
            verdict = SecurityVerdict(
                verdict=SecurityVerdictType.ALLOW,
                reason="Security subsystem disabled",
                risk_level=ApprovalRiskLevel.LOW,
                evaluated_at=datetime.now(UTC),
                evaluation_duration_ms=0.0,
            )
            if self._config.audit_enabled:
                self._record_audit(context, verdict)
            return verdict

        logger.info(
            SECURITY_EVALUATE_START,
            tool_name=context.tool_name,
            action_type=context.action_type,
            agent_id=context.agent_id,
        )

        # Always run the rule engine first — security detectors must
        # never be bypassed, regardless of autonomy configuration.
        try:
            verdict = self._rule_engine.evaluate(context)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_INTERCEPTOR_ERROR,
                tool_name=context.tool_name,
                note="Rule engine evaluation failed (fail-closed)",
            )
            verdict = SecurityVerdict(
                verdict=SecurityVerdictType.DENY,
                reason="Rule engine evaluation failed (fail-closed)",
                risk_level=ApprovalRiskLevel.CRITICAL,
                evaluated_at=datetime.now(UTC),
                evaluation_duration_ms=0.0,
            )

        # Apply autonomy augmentation *after* the rule engine.
        # Autonomy can only add stricter requirements (ALLOW → ESCALATE),
        # never weaken a DENY or ESCALATE from security detectors.
        verdict = self._apply_autonomy_augmentation(context, verdict)

        # Handle escalation.
        if verdict.verdict == SecurityVerdictType.ESCALATE:
            verdict = await self._handle_escalation(context, verdict)

        # Record audit.
        if self._config.audit_enabled:
            self._record_audit(context, verdict)

        # Log verdict.
        event = {
            SecurityVerdictType.ALLOW: SECURITY_VERDICT_ALLOW,
            SecurityVerdictType.DENY: SECURITY_VERDICT_DENY,
            SecurityVerdictType.ESCALATE: SECURITY_VERDICT_ESCALATE,
        }.get(verdict.verdict, SECURITY_EVALUATE_COMPLETE)
        logger.info(
            event,
            tool_name=context.tool_name,
            verdict=verdict.verdict.value,
            risk_level=verdict.risk_level.value,
        )

        return verdict

    async def scan_output(
        self,
        context: SecurityContext,
        output: str,
    ) -> OutputScanResult:
        """Scan tool output for sensitive data.

        Steps:
            1. Delegate to the output scanner.
            2. Record an audit entry if sensitive data is found.
            3. Apply the output scan response policy to transform
               the result before returning.
        """
        if not self._config.post_tool_scanning_enabled:
            logger.debug(
                SECURITY_EVALUATE_COMPLETE,
                note="output scanning disabled",
                tool_name=context.tool_name,
            )
            return OutputScanResult()

        result = self._output_scanner.scan(output)

        if result.has_sensitive_data and self._config.audit_enabled:
            entry = AuditEntry(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(UTC),
                agent_id=context.agent_id,
                task_id=context.task_id,
                tool_name=context.tool_name,
                tool_category=context.tool_category,
                action_type=context.action_type,
                arguments_hash=_hash_arguments(context.arguments),
                verdict=OUTPUT_SCAN_VERDICT,
                risk_level=ApprovalRiskLevel.HIGH,
                reason=("Sensitive data in output: " + ", ".join(result.findings)),
                evaluation_duration_ms=0.0,
            )
            try:
                self._audit_log.record(entry)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    SECURITY_AUDIT_RECORD_ERROR,
                    tool_name=context.tool_name,
                    note="Output scan audit recording failed",
                )

        # Apply the output scan response policy.  On failure, fall back
        # to the raw scan result which already has scanner-level redaction
        # applied (pattern matches replaced with [REDACTED]), so the
        # fallback is still reasonably safe even if the intended policy
        # (e.g. WithholdPolicy) would have been stricter.
        policy_name = getattr(self._output_scan_policy, "name", "<unknown>")
        try:
            result = self._output_scan_policy.apply(result, context)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_INTERCEPTOR_ERROR,
                tool_name=context.tool_name,
                policy=policy_name,
                fallback_outcome=result.outcome.value,
                note="Output scan policy application failed "
                "— returning raw scan result "
                "(may be less strict than intended policy)",
            )

        return result

    def _apply_autonomy_augmentation(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
    ) -> SecurityVerdict:
        """Augment the rule engine verdict with autonomy routing.

        Autonomy can only *tighten* a verdict (ALLOW → ESCALATE), never
        weaken one.  DENY and ESCALATE from the rule engine are always
        preserved — security detectors take precedence over autonomy.

        Returns the (possibly upgraded) verdict.
        """
        if self._effective_autonomy is None:
            return verdict

        # Security DENY/ESCALATE always takes precedence.
        if verdict.verdict != SecurityVerdictType.ALLOW:
            return verdict

        action = context.action_type
        autonomy = self._effective_autonomy

        if action in autonomy.auto_approve_actions:
            logger.info(
                AUTONOMY_ACTION_AUTO_APPROVED,
                tool_name=context.tool_name,
                action_type=action,
                autonomy_level=autonomy.level.value,
            )
            return verdict

        if action in autonomy.human_approval_actions:
            risk_level = (
                self._risk_classifier.classify(action)
                if self._risk_classifier
                else ApprovalRiskLevel.HIGH
            )
            logger.info(
                AUTONOMY_ACTION_HUMAN_REQUIRED,
                tool_name=context.tool_name,
                action_type=action,
                autonomy_level=autonomy.level.value,
                risk_level=risk_level.value,
            )
            return verdict.model_copy(
                update={
                    "verdict": SecurityVerdictType.ESCALATE,
                    "reason": (
                        f"Human approval required by autonomy level "
                        f"'{autonomy.level.value}'"
                    ),
                    "risk_level": risk_level,
                },
            )

        # Not classified by autonomy — keep rule engine's verdict.
        return verdict

    def _record_audit(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
    ) -> None:
        """Record an audit entry for a pre-tool evaluation.

        Model construction errors propagate (they indicate programming
        bugs).  Storage errors are caught and logged — they must never
        prevent the verdict from being returned.
        """
        entry = AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=verdict.evaluated_at,
            agent_id=context.agent_id,
            task_id=context.task_id,
            tool_name=context.tool_name,
            tool_category=context.tool_category,
            action_type=context.action_type,
            arguments_hash=_hash_arguments(context.arguments),
            verdict=verdict.verdict.value,
            risk_level=verdict.risk_level,
            reason=verdict.reason,
            matched_rules=verdict.matched_rules,
            evaluation_duration_ms=verdict.evaluation_duration_ms,
            approval_id=verdict.approval_id,
        )
        try:
            self._audit_log.record(entry)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_AUDIT_RECORD_ERROR,
                tool_name=context.tool_name,
                note="Audit recording failed — verdict still returned",
            )

    async def _handle_escalation(
        self,
        context: SecurityContext,
        verdict: SecurityVerdict,
    ) -> SecurityVerdict:
        """Create an approval item in the approval store.

        Falls back to DENY if no approval store is configured or if
        the store raises an exception.
        """
        if self._approval_store is None:
            logger.warning(
                SECURITY_VERDICT_DENY,
                tool_name=context.tool_name,
                original_verdict="escalate",
                note="no approval store — converting to DENY",
            )
            return verdict.model_copy(
                update={
                    "verdict": SecurityVerdictType.DENY,
                    "reason": (f"{verdict.reason} (escalation unavailable — denied)"),
                },
            )

        approval_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        item = ApprovalItem(
            id=approval_id,
            action_type=context.action_type,
            title=f"Security escalation: {context.tool_name}",
            description=verdict.reason,
            requested_by=context.agent_id or "system",
            risk_level=verdict.risk_level,
            status=ApprovalStatus.PENDING,
            created_at=now,
            task_id=context.task_id,
            metadata={
                "tool_name": context.tool_name,
                "tool_category": context.tool_category.value,
            },
        )
        try:
            await self._approval_store.add(item)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                SECURITY_ESCALATION_STORE_ERROR,
                approval_id=approval_id,
                tool_name=context.tool_name,
                agent_id=context.agent_id,
            )
            return verdict.model_copy(
                update={
                    "verdict": SecurityVerdictType.DENY,
                    "reason": (f"{verdict.reason} (escalation store error — denied)"),
                },
            )
        logger.info(
            SECURITY_ESCALATION_CREATED,
            approval_id=approval_id,
            tool_name=context.tool_name,
            agent_id=context.agent_id,
        )
        return verdict.model_copy(
            update={"approval_id": approval_id},
        )

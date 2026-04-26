"""Security event constants."""

from typing import Final

SECURITY_EVALUATE_START: Final[str] = "security.evaluate.start"
SECURITY_EVALUATE_COMPLETE: Final[str] = "security.evaluate.complete"
SECURITY_RULE_MATCHED: Final[str] = "security.rule.matched"
SECURITY_RULE_ERROR: Final[str] = "security.rule.error"
SECURITY_VERDICT_ALLOW: Final[str] = "security.verdict.allow"
SECURITY_VERDICT_DENY: Final[str] = "security.verdict.deny"
SECURITY_VERDICT_ESCALATE: Final[str] = "security.verdict.escalate"
SECURITY_AUDIT_RECORDED: Final[str] = "security.audit.recorded"
SECURITY_AUDIT_EVICTION: Final[str] = "security.audit.eviction"
SECURITY_AUDIT_CLEARED: Final[str] = "security.audit.cleared"
SECURITY_OUTPUT_SCAN_START: Final[str] = "security.output_scan.start"
SECURITY_OUTPUT_SCAN_FINDING: Final[str] = "security.output_scan.finding"
SECURITY_ESCALATION_CREATED: Final[str] = "security.escalation.created"
SECURITY_ESCALATION_STORE_ERROR: Final[str] = "security.escalation.store_error"
SECURITY_CONFIG_LOADED: Final[str] = "security.config.loaded"
SECURITY_DISABLED: Final[str] = "security.disabled"
SECURITY_RISK_FALLBACK: Final[str] = "security.risk.fallback"
SECURITY_CREDENTIAL_DETECTED: Final[str] = "security.credential.detected"
SECURITY_PATH_TRAVERSAL_DETECTED: Final[str] = "security.path_traversal.detected"
SECURITY_DESTRUCTIVE_OP_DETECTED: Final[str] = "security.destructive_op.detected"
SECURITY_DATA_LEAK_DETECTED: Final[str] = "security.data_leak.detected"
SECURITY_POLICY_DENY: Final[str] = "security.policy.deny"
SECURITY_POLICY_AUTO_APPROVE: Final[str] = "security.policy.auto_approve"
SECURITY_INTERCEPTOR_ERROR: Final[str] = "security.interceptor.error"
SECURITY_OUTPUT_SCAN_ERROR: Final[str] = "security.output_scan.error"
SECURITY_AUDIT_CONFIG_ERROR: Final[str] = "security.audit.config_error"
SECURITY_SCAN_DEPTH_EXCEEDED: Final[str] = "security.scan.depth_exceeded"
SECURITY_AUDIT_RECORD_ERROR: Final[str] = "security.audit.record_error"
SECURITY_ACTION_TYPE_INVALID: Final[str] = "security.action_type.invalid"
SECURITY_OUTPUT_SCAN_POLICY_APPLIED: Final[str] = "security.output_scan.policy_applied"

# LLM fallback evaluation events.
SECURITY_LLM_EVAL_START: Final[str] = "security.llm_eval.start"
SECURITY_LLM_EVAL_COMPLETE: Final[str] = "security.llm_eval.complete"
SECURITY_LLM_EVAL_ERROR: Final[str] = "security.llm_eval.error"
SECURITY_LLM_EVAL_TIMEOUT: Final[str] = "security.llm_eval.timeout"
SECURITY_LLM_EVAL_CROSS_FAMILY: Final[str] = "security.llm_eval.cross_family"
SECURITY_LLM_EVAL_SAME_FAMILY_FALLBACK: Final[str] = (
    "security.llm_eval.same_family_fallback"
)
SECURITY_LLM_EVAL_NO_PROVIDER: Final[str] = "security.llm_eval.no_provider"
SECURITY_LLM_EVAL_SKIPPED_FULL_AUTONOMY: Final[str] = (
    "security.llm_eval.skipped_full_autonomy"
)

# ── Audit chain events ──────────────────────────────────────────
SECURITY_AUDIT_CHAIN_SIGNED: Final[str] = "security.audit_chain.signed"
SECURITY_AUDIT_CHAIN_VERIFY_START: Final[str] = "security.audit_chain.verify.start"
SECURITY_AUDIT_CHAIN_VERIFY_COMPLETE: Final[str] = (
    "security.audit_chain.verify.complete"
)
SECURITY_AUDIT_CHAIN_BREAK_DETECTED: Final[str] = "security.audit_chain.break_detected"
SECURITY_TIMESTAMP_FALLBACK: Final[str] = "security.timestamp.fallback"
SECURITY_TIMESTAMP_REQUESTED: Final[str] = "security.timestamp.requested"
SECURITY_TIMESTAMP_GRANTED: Final[str] = "security.timestamp.granted"
SECURITY_TIMESTAMP_REJECTED: Final[str] = "security.timestamp.rejected"
SECURITY_TIMESTAMP_TIMEOUT: Final[str] = "security.timestamp.timeout"
SECURITY_TIMESTAMP_TRANSPORT_ERROR: Final[str] = "security.timestamp.transport_error"
SECURITY_TIMESTAMP_PROTOCOL_ERROR: Final[str] = "security.timestamp.protocol_error"
SECURITY_TIMESTAMP_HASH_MISMATCH: Final[str] = "security.timestamp.hash_mismatch"
SECURITY_TIMESTAMP_NONCE_MISMATCH: Final[str] = "security.timestamp.nonce_mismatch"
SECURITY_TIMESTAMP_SIGNATURE_INVALID: Final[str] = (
    "security.timestamp.signature_invalid"
)
SECURITY_TIMESTAMP_INCIDENT: Final[str] = "security.timestamp.incident"

# Audit chain sink internal errors live in ``events.audit_chain``
# with a non-"security." prefix so they can't recurse through
# ``AuditChainSink.emit``. Policy-engine events below are unaffected.

# ── Policy engine events ────────────────────────────────────────
SECURITY_POLICY_EVALUATE_START: Final[str] = "security.policy.evaluate.start"
SECURITY_POLICY_DECISION_ALLOW: Final[str] = "security.policy.decision.allow"
SECURITY_POLICY_DECISION_DENY: Final[str] = "security.policy.decision.deny"
SECURITY_POLICY_ENGINE_ERROR: Final[str] = "security.policy.engine.error"
SECURITY_POLICY_LOG_ONLY_DENY: Final[str] = "security.policy.log_only.deny"

# Custom policy events.
SECURITY_CUSTOM_POLICY_MATCHED: Final[str] = "security.custom_policy.matched"

# Shadow mode events.
SECURITY_SHADOW_WOULD_BLOCK: Final[str] = "security.shadow.would_block"

# Safety classifier events.
SECURITY_SAFETY_CLASSIFY_START: Final[str] = "security.safety_classify.start"
SECURITY_SAFETY_CLASSIFY_COMPLETE: Final[str] = "security.safety_classify.complete"
SECURITY_SAFETY_CLASSIFY_ERROR: Final[str] = "security.safety_classify.error"
SECURITY_SAFETY_CLASSIFY_BLOCKED: Final[str] = "security.safety_classify.blocked"
SECURITY_SAFETY_CLASSIFY_SUSPICIOUS: Final[str] = "security.safety_classify.suspicious"
SECURITY_INFO_STRIP_COMPLETE: Final[str] = "security.info_strip.complete"

# Uncertainty check events.
SECURITY_UNCERTAINTY_CHECK_START: Final[str] = "security.uncertainty_check.start"
SECURITY_UNCERTAINTY_CHECK_COMPLETE: Final[str] = "security.uncertainty_check.complete"
SECURITY_UNCERTAINTY_CHECK_ERROR: Final[str] = "security.uncertainty_check.error"
SECURITY_UNCERTAINTY_CHECK_SKIPPED: Final[str] = "security.uncertainty_check.skipped"
SECURITY_UNCERTAINTY_LOW_CONFIDENCE: Final[str] = "security.uncertainty.low_confidence"

# Denial tracker events.
SECURITY_DENIAL_RECORDED: Final[str] = "security.denial.recorded"
SECURITY_DENIAL_ESCALATED: Final[str] = "security.denial.escalated"
SECURITY_DENIAL_RESET: Final[str] = "security.denial.reset"

# Permission tier events.
SECURITY_TIER_SAFE_TOOL: Final[str] = "security.tier.safe_tool"
SECURITY_TIER_CLASSIFIED: Final[str] = "security.tier.classified"

# Risk tier override events.
SECURITY_RISK_OVERRIDE_CREATED: Final[str] = "security.risk_override.created"
SECURITY_RISK_OVERRIDE_REVOKED: Final[str] = "security.risk_override.revoked"
SECURITY_RISK_OVERRIDE_APPLIED: Final[str] = "security.risk_override.applied"
SECURITY_RISK_OVERRIDE_EXPIRED: Final[str] = "security.risk_override.expired"

# SSRF violation events.
SECURITY_SSRF_VIOLATION_RECORDED: Final[str] = "security.ssrf_violation.recorded"
SECURITY_SSRF_VIOLATION_ALLOWED: Final[str] = "security.ssrf_violation.allowed"
SECURITY_SSRF_VIOLATION_DENIED: Final[str] = "security.ssrf_violation.denied"
SECURITY_ALLOWLIST_UPDATED: Final[str] = "security.allowlist.updated"
SECURITY_ALLOWLIST_UPDATE_FAILED: Final[str] = "security.allowlist.update_failed"

# ── Authentication events (signed by the audit chain) ──────────
# These represent security decisions: who logged in (or didn't), who
# changed their password, when an account was locked, when a refresh
# token was minted/consumed/rejected. Operational events (cleanup,
# fallback, listing) stay under api.* and are not signed.
SECURITY_AUTH_SUCCESS: Final[str] = "security.auth.success"
SECURITY_AUTH_FAILED: Final[str] = "security.auth.failed"
SECURITY_AUTH_TOKEN_ISSUED: Final[str] = "security.auth.token_issued"  # noqa: S105
SECURITY_AUTH_PASSWORD_CHANGED: Final[str] = "security.auth.password_changed"  # noqa: S105
SECURITY_AUTH_SETUP_COMPLETE: Final[str] = "security.auth.setup_complete"
SECURITY_AUTH_ACCOUNT_LOCKED: Final[str] = "security.auth.account_locked"
SECURITY_AUTH_LOCKOUT_CLEARED: Final[str] = "security.auth.lockout_cleared"
SECURITY_AUTH_REFRESH_CREATED: Final[str] = "security.auth.refresh_created"
SECURITY_AUTH_REFRESH_CONSUMED: Final[str] = "security.auth.refresh_consumed"
SECURITY_AUTH_REFRESH_REJECTED: Final[str] = "security.auth.refresh_rejected"
SECURITY_AUTH_REFRESH_REVOKED: Final[str] = "security.auth.refresh_revoked"

# ── Session events (signed) ────────────────────────────────────
SECURITY_SESSION_CREATED: Final[str] = "security.session.created"
SECURITY_SESSION_REVOKED: Final[str] = "security.session.revoked"
SECURITY_SESSION_FORCE_LOGOUT: Final[str] = "security.session.force_logout"
SECURITY_SESSION_LIMIT_ENFORCED: Final[str] = "security.session.limit_enforced"

# ── CSRF rejection (signed) ────────────────────────────────────
SECURITY_CSRF_REJECTED: Final[str] = "security.csrf.rejected"

# ── User CRUD (signed) ─────────────────────────────────────────
# Listing and save-failure events stay under api.user.* (operational).
SECURITY_USER_CREATED: Final[str] = "security.user.created"
SECURITY_USER_UPDATED: Final[str] = "security.user.updated"
SECURITY_USER_DELETED: Final[str] = "security.user.deleted"

# ── Approval decisions (signed) ────────────────────────────────
# Creation, expiration, and review-completion stay under api.approval.*
# / approval_gate.* (request lifecycle, not the human decision).
SECURITY_APPROVAL_APPROVED: Final[str] = "security.approval.approved"
SECURITY_APPROVAL_REJECTED: Final[str] = "security.approval.rejected"
SECURITY_APPROVAL_DECISION_RECORDED: Final[str] = "security.approval.decision.recorded"
SECURITY_APPROVAL_SELF_REVIEW_PREVENTED: Final[str] = (
    "security.approval.self_review.prevented"
)

# ── Autonomy lifecycle (signed) ────────────────────────────────
# Per-action auto-approve / human-required events stay under
# autonomy.* (high-frequency runtime hot path).
SECURITY_AUTONOMY_PROMOTION_REQUESTED: Final[str] = (
    "security.autonomy.promotion.requested"
)
SECURITY_AUTONOMY_PROMOTION_DENIED: Final[str] = "security.autonomy.promotion.denied"
SECURITY_AUTONOMY_DOWNGRADE_TRIGGERED: Final[str] = (
    "security.autonomy.downgrade.triggered"
)
SECURITY_AUTONOMY_RECOVERY_REQUESTED: Final[str] = (
    "security.autonomy.recovery.requested"
)

# ── Provider / API-key management (signed) ─────────────────────
# Validation failures stay under provider.management.* (operational).
SECURITY_PROVIDER_CREATED: Final[str] = "security.provider.created"
SECURITY_PROVIDER_UPDATED: Final[str] = "security.provider.updated"
SECURITY_PROVIDER_DELETED: Final[str] = "security.provider.deleted"

# ── Settings change (signed when sensitive namespace) ──────────
# Emitted by SettingsService.set when the namespace is in the
# sensitive set (auth, security, autonomy, encryption, rbac).
SECURITY_SETTINGS_CHANGED: Final[str] = "security.settings.changed"

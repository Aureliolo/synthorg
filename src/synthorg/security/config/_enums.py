"""Enum discriminators for security configuration."""

from enum import StrEnum


class SecurityEnforcementMode(StrEnum):
    """Security enforcement mode for the SecOps service.

    Controls whether security verdicts are enforced, logged only
    (shadow mode for calibration), or fully disabled.

    Members:
        ACTIVE: Full enforcement -- verdicts are applied as-is.
        SHADOW: Shadow mode -- full evaluation pipeline runs and
            audit entries are recorded, but blocking verdicts (DENY,
            ESCALATE) are converted to ALLOW.  Used for pre-deployment
            calibration of risk budgets.
        DISABLED: Security subsystem is disabled -- no evaluation,
            always ALLOW.
    """

    ACTIVE = "active"
    SHADOW = "shadow"
    DISABLED = "disabled"


class OutputScanPolicyType(StrEnum):
    """Declarative output scan policy selection.

    Used in ``SecurityConfig`` to select the output scan response
    policy at config time.  Runtime constructor injection is also
    supported for full flexibility.

    Members:
        REDACT: Return redacted content (scanner-level redaction).
        WITHHOLD: Clear redacted content, forcing fail-closed.
        LOG_ONLY: Log findings but pass output through.
        AUTONOMY_TIERED: Delegate based on effective autonomy level
            (default -- falls back to ``REDACT`` when no autonomy
            is configured).
    """

    REDACT = "redact"
    WITHHOLD = "withhold"
    LOG_ONLY = "log_only"
    AUTONOMY_TIERED = "autonomy_tiered"


class VerdictReasonVisibility(StrEnum):
    """Controls how much of the LLM evaluator's reason is visible to agents.

    Attributes:
        FULL: Return the full LLM reason to the agent.
        GENERIC: Return a generic denial/escalation message.
        CATEGORY: Return verdict type and risk level only.
    """

    FULL = "full"
    GENERIC = "generic"
    CATEGORY = "category"


class ArgumentTruncationStrategy(StrEnum):
    """How to truncate large tool arguments for the LLM security prompt.

    Attributes:
        WHOLE_STRING: Truncate the serialized JSON at a character limit.
        PER_VALUE: Truncate each argument value individually before
            serialization, preserving all key names.
        KEYS_AND_VALUES: Include all keys with individually capped
            values (explicit about key preservation).
    """

    WHOLE_STRING = "whole_string"
    PER_VALUE = "per_value"
    KEYS_AND_VALUES = "keys_and_values"


class LlmFallbackErrorPolicy(StrEnum):
    """What to do when the LLM security evaluation fails.

    Attributes:
        USE_RULE_VERDICT: Fall back to the original rule engine verdict.
        ESCALATE: Send the action to the human approval queue.
        DENY: Deny the action (fail-closed).
    """

    USE_RULE_VERDICT = "use_rule_verdict"
    ESCALATE = "escalate"
    DENY = "deny"


class McpSelfConsumerMode(StrEnum):
    """Dispatch token for the agent -> SynthOrg-MCP self-consumer.

    ``DISABLED`` (default, safe) wires no bridge: a running agent
    cannot call SynthOrg's own MCP tools. ``TRUST_SCOPED`` exposes
    the MCP surface to the agent's tool invoker, scoped by the
    agent's earned trust level (ELEVATED gets the full capability
    set; everything below is restricted to the explicit
    ``read_tool_allowlist``).
    """

    DISABLED = "disabled"
    TRUST_SCOPED = "trust_scoped"


class VisionVerifierKind(StrEnum):
    """Discriminator selecting a concrete vision verifier strategy."""

    NOOP = "noop"
    HEURISTIC = "heuristic"
    LLM_VISION = "llm_vision"

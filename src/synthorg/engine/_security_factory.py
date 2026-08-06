"""Security and tool factories for AgentEngine.

Extracted from ``agent_engine.py`` to keep that module within the
800-line limit.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.engine.errors import ExecutionStateError
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_CONFIG_LOADED,
    SECURITY_DISABLED,
    SECURITY_RISK_FALLBACK,
)
from synthorg.observability.events.timeout import TIMEOUT_UNKNOWN_ACTION_TYPE
from synthorg.providers.registry import ProviderRegistry
from synthorg.providers.routing.resolver import ModelResolver
from synthorg.security.audit import AuditLog
from synthorg.security.config import SecurityConfig
from synthorg.security.output_scanner import OutputScanner
from synthorg.security.protocol import SecurityInterceptionStrategy
from synthorg.security.risk_map import default_risk_classifier
from synthorg.security.rules.credential_detector import CredentialDetector
from synthorg.security.rules.custom_policy_rule import CustomPolicyRule
from synthorg.security.rules.data_leak_detector import DataLeakDetector
from synthorg.security.rules.destructive_op_detector import (
    DestructiveOpDetector,
)
from synthorg.security.rules.engine import RuleEngine
from synthorg.security.rules.mcp_destructive_op_detector import (
    MCPDestructiveOpDetector,
)
from synthorg.security.rules.path_traversal_detector import (
    PathTraversalDetector,
)
from synthorg.security.rules.policy_validator import PolicyValidator
from synthorg.security.service import SecOpsService
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.base import BaseTool
from synthorg.tools.external_api._runtime import ExternalApiRuntime
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.effective_autonomy import EffectiveAutonomy

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SecurityLlmInfra:
    """Provider infrastructure the LLM-backed security features dispatch on.

    Travels as one value because the three LLM-backed features are wired
    together or not at all: without a registry there is nothing to dispatch
    on, and without a resolver there is no operator-chosen connection to
    dispatch to.

    Attributes:
        provider_registry: Registry of provider drivers.
        config_resolver: Live source of each feature's own
            ``(provider, model)`` assignment, re-read per evaluation.
            Required, not optional: without it no assignment can be read, so
            a feature built with a registry alone dispatches nowhere while
            reporting itself enabled.
        provider_configs: Provider configs keyed by name, for the vendor
            family comparison the LLM evaluator warns on.
        model_resolver: Model resolver for the multi-provider uncertainty
            check, which deliberately fans out across providers.
        cost_tracker: Cost tracker so security-evaluation calls emit
            ``CostRecord``s through the provider chokepoint instead of
            bypassing the cost-recording layer.
    """

    provider_registry: ProviderRegistry
    config_resolver: ConfigResolverProtocol
    provider_configs: Mapping[str, ProviderConfig] = field(default_factory=dict)
    model_resolver: ModelResolver | None = None
    cost_tracker: CostTrackerProtocol | None = None


def make_security_interceptor(
    security_config: SecurityConfig | None,
    audit_log: AuditLog,
    *,
    approval_store: ApprovalStoreProtocol | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
    llm_infra: SecurityLlmInfra | None = None,
) -> SecurityInterceptionStrategy | None:
    """Build the SecOps security interceptor if configured.

    Args:
        security_config: Security configuration, or ``None`` to skip.
        audit_log: Audit log for security events.
        approval_store: Optional approval store for escalation items.
        effective_autonomy: Optional autonomy level override.
        llm_infra: Provider infrastructure for the LLM-backed features
            (safety classifier, uncertainty checker, LLM fallback
            evaluator); ``None`` leaves all three unwired.

    Returns:
        A ``SecOpsService`` interceptor, or ``None`` if security is
        disabled or not configured.

    Raises:
        ExecutionStateError: If *effective_autonomy* is provided but
            no SecurityConfig is configured.
    """
    if security_config is None:
        if effective_autonomy is not None:
            msg = (
                "effective_autonomy cannot be enforced without "
                "SecurityConfig -- configure security or remove autonomy"
            )
            logger.error(SECURITY_DISABLED, note=msg)
            raise ExecutionStateError(msg)
        logger.warning(
            SECURITY_DISABLED,
            note="No SecurityConfig provided -- all security checks skipped",
        )
        return None
    if not security_config.enabled:
        if effective_autonomy is not None:
            msg = "effective_autonomy cannot be enforced when security is disabled"
            logger.error(SECURITY_DISABLED, note=msg)
            raise ExecutionStateError(msg)
        return None

    cfg = security_config
    rule_engine = _build_rule_engine(cfg)

    # Build optional LLM-based services when provider infrastructure is
    # available. The bundle narrows once, so every constructor below sees
    # non-``None`` provider infrastructure.
    llm_evaluator = None
    safety_classifier = None
    denial_tracker = None
    uncertainty_checker = None

    if llm_infra is None:
        # Warn when LLM-based features are configured but providers are
        # not available -- the features will be silently disabled.
        _warn_disabled_features(cfg)
    else:
        if cfg.llm_fallback.enabled:
            from synthorg.security.llm_evaluator import (  # noqa: PLC0415
                LlmSecurityEvaluator,
            )

            llm_evaluator = LlmSecurityEvaluator(
                provider_registry=llm_infra.provider_registry,
                provider_configs=llm_infra.provider_configs,
                config=cfg.llm_fallback,
                config_resolver=llm_infra.config_resolver,
            )

        if cfg.safety_classifier.enabled:
            from synthorg.security.denial_tracker import (  # noqa: PLC0415
                DenialTracker,
            )
            from synthorg.security.safety_classifier import (  # noqa: PLC0415
                SafetyClassifier,
            )

            safety_classifier = SafetyClassifier(
                provider_registry=llm_infra.provider_registry,
                config=cfg.safety_classifier,
                cost_tracker=llm_infra.cost_tracker,
                config_resolver=llm_infra.config_resolver,
            )
            denial_tracker = DenialTracker(
                max_consecutive=cfg.safety_classifier.max_consecutive_denials,
                max_total=cfg.safety_classifier.max_total_denials,
            )

        if llm_infra.model_resolver is not None and cfg.uncertainty_check.enabled:
            from synthorg.security.uncertainty import (  # noqa: PLC0415
                UncertaintyChecker,
            )

            uncertainty_checker = UncertaintyChecker(
                provider_registry=llm_infra.provider_registry,
                model_resolver=llm_infra.model_resolver,
                config=cfg.uncertainty_check,
                cost_tracker=llm_infra.cost_tracker,
            )

    return SecOpsService(
        config=cfg,
        rule_engine=rule_engine,
        audit_log=audit_log,
        output_scanner=OutputScanner(),
        approval_store=approval_store,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
        llm_evaluator=llm_evaluator,
        safety_classifier=safety_classifier,
        uncertainty_checker=uncertainty_checker,
        denial_tracker=denial_tracker,
    )


def _build_rule_engine(cfg: SecurityConfig) -> RuleEngine:
    """Assemble the rule engine with built-in detectors and custom policies.

    Returns:
        A :class:`RuleEngine` whose rule list is the policy validator,
        the enabled built-in detectors, and the configured custom
        policies (ordered per the bypass flag).
    """
    re_cfg = cfg.rule_engine
    policy_validator = PolicyValidator(
        hard_deny_action_types=frozenset(cfg.hard_deny_action_types),
        auto_approve_action_types=frozenset(cfg.auto_approve_action_types),
    )
    rules: list[
        PolicyValidator
        | CredentialDetector
        | PathTraversalDetector
        | DestructiveOpDetector
        | MCPDestructiveOpDetector
        | DataLeakDetector
        | CustomPolicyRule
    ] = [policy_validator]

    # When custom_allow_bypasses_detectors is True, custom policies go
    # right after the policy validator (before detectors) so a custom
    # ALLOW can short-circuit security scanning.  Otherwise (default),
    # custom policies go after all detectors -- security scanning
    # always runs first.
    custom_rules = [CustomPolicyRule(p) for p in cfg.custom_policies if p.enabled]
    if re_cfg.custom_allow_bypasses_detectors:
        rules.extend(custom_rules)

    if re_cfg.credential_patterns_enabled:
        rules.append(CredentialDetector())
    if re_cfg.path_traversal_detection_enabled:
        rules.append(PathTraversalDetector())
    if re_cfg.destructive_op_detection_enabled:
        rules.append(DestructiveOpDetector())
    if re_cfg.mcp_destructive_op_detection_enabled:
        rules.append(MCPDestructiveOpDetector())
    if re_cfg.data_leak_detection_enabled:
        rules.append(DataLeakDetector())

    if not re_cfg.custom_allow_bypasses_detectors:
        rules.extend(custom_rules)

    if custom_rules:
        log_level = (
            logger.warning if re_cfg.custom_allow_bypasses_detectors else logger.debug
        )
        log_level(
            SECURITY_CONFIG_LOADED,
            custom_policy_count=len(custom_rules),
            bypasses_detectors=re_cfg.custom_allow_bypasses_detectors,
        )

    return RuleEngine(
        rules=tuple(rules),
        risk_classifier=default_risk_classifier(miss_event=SECURITY_RISK_FALLBACK),
        config=re_cfg,
    )


def _warn_disabled_features(cfg: SecurityConfig) -> None:
    """Log warnings for enabled LLM features with no providers."""
    features = []
    if cfg.llm_fallback.enabled:
        features.append("llm_fallback")
    if cfg.safety_classifier.enabled:
        features.append("safety_classifier")
    if cfg.uncertainty_check.enabled:
        features.append("uncertainty_check")
    if features:
        logger.warning(
            SECURITY_CONFIG_LOADED,
            note=(
                "LLM-based security features are enabled but no "
                "provider infrastructure was supplied -- these "
                "features will be inactive"
            ),
            disabled_features=", ".join(features),
        )


def _registry_with_tool_appended(
    tool_registry: ToolRegistry, tool: BaseTool
) -> ToolRegistry:
    """Return a new registry carrying the existing tools plus *tool*.

    Returns:
        A :class:`ToolRegistry` with *tool* appended.
    """
    return ToolRegistry([*tool_registry.all_tools(), tool])


def registry_with_approval_tool(
    tool_registry: ToolRegistry,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
) -> ToolRegistry:
    """Build a registry with the approval tool added if applicable.

    Returns:
        A :class:`ToolRegistry` with the approval tool appended when
        an approval store is configured; the original registry
        unchanged when ``approval_store`` is ``None``.
    """
    if approval_store is None:
        return tool_registry

    from synthorg.tools.approval_tool import (  # noqa: PLC0415
        RequestHumanApprovalTool,
    )

    approval_tool = RequestHumanApprovalTool(
        approval_store=approval_store,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
        agent_id=str(identity.id),
        task_id=task_id,
    )
    return _registry_with_tool_appended(tool_registry, approval_tool)


def registry_with_clarification_tool(
    tool_registry: ToolRegistry,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
) -> ToolRegistry:
    """Add the mid-task clarification tool when clarification is enabled.

    Gated by the caller (``engine.clarification_enabled``); this helper
    only checks that an approval store is available to persist the parked
    clarification. Returns the registry unchanged when no store is wired.

    Returns:
        A :class:`ToolRegistry` with the clarification tool appended when
        an approval store is configured; the original registry unchanged
        otherwise.
    """
    if approval_store is None:
        return tool_registry

    from synthorg.tools.clarification_tool import (  # noqa: PLC0415
        RequestClarificationTool,
    )

    clarification_tool = RequestClarificationTool(
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
    )
    return _registry_with_tool_appended(tool_registry, clarification_tool)


def registry_with_decision_tool(
    tool_registry: ToolRegistry,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
) -> ToolRegistry:
    """Add the project-decision tool when scoping is enabled.

    Gated by the caller (``engine.scoping_enabled``); this helper only checks
    that an approval store is available to persist the parked decision.
    Returns the registry unchanged when no store is wired.

    Returns:
        A :class:`ToolRegistry` with the decision tool appended when an
        approval store is configured; the original registry unchanged
        otherwise.
    """
    if approval_store is None:
        return tool_registry

    from synthorg.tools.decision_tool import (  # noqa: PLC0415
        RequestProjectDecisionTool,
    )

    decision_tool = RequestProjectDecisionTool(
        approval_store=approval_store,
        agent_id=str(identity.id),
        task_id=task_id,
    )
    return _registry_with_tool_appended(tool_registry, decision_tool)


def registry_with_human_input_tools(
    tool_registry: ToolRegistry,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    *,
    clarification_enabled: bool,
    scoping_enabled: bool,
) -> ToolRegistry:
    """Add the enabled mid-task human-input tools to the registry.

    Composes the clarification and project-decision tools per their gate
    flags (``engine.clarification_enabled`` / ``engine.scoping_enabled``), so
    the per-run factory attaches them in one call.

    Returns:
        The registry extended with whichever human-input tools are enabled.
    """
    registry = tool_registry
    if clarification_enabled:
        registry = registry_with_clarification_tool(
            registry, approval_store, identity, task_id
        )
    if scoping_enabled:
        registry = registry_with_decision_tool(
            registry, approval_store, identity, task_id
        )
    return registry


def registry_with_external_api_tool(
    tool_registry: ToolRegistry,
    runtime: ExternalApiRuntime | None,
    *,
    approval_store: ApprovalStoreProtocol | None,
    identity: AgentIdentity,
    task_id: str | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
) -> ToolRegistry:
    """Add the governed external-access tool when its runtime is wired.

    Returns the registry unchanged when no runtime bundle is present (the
    feature is disabled or no connection catalog is configured) or when no
    approval store is available (sensitive calls could not be gated). The
    tool is run-scoped: it binds the run's identity, task, and effective
    autonomy alongside the boot-scoped catalog / provider / policy.

    Returns:
        A :class:`ToolRegistry` with the external-API tool appended
        when both ``runtime`` and ``approval_store`` are wired;
        otherwise the original registry unchanged.
    """
    if runtime is None or approval_store is None:
        return tool_registry

    from synthorg.tools.external_api.external_api_tool import (  # noqa: PLC0415
        ExternalApiTool,
    )
    from synthorg.tools.registry import (  # noqa: PLC0415
        ToolRegistry as _ToolRegistry,
    )

    external_api_tool = ExternalApiTool(
        connection_catalog=runtime.connection_catalog,
        approval_store=approval_store,
        provider=runtime.provider,
        agent_id=str(identity.id),
        task_id=task_id,
        network_policy=runtime.network_policy,
        effective_autonomy=effective_autonomy,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
        max_response_bytes=runtime.max_response_bytes,
        timeout_seconds=runtime.timeout_seconds,
        default_max_rpm=runtime.default_max_rpm,
    )
    existing = list(tool_registry.all_tools())
    return _ToolRegistry([*existing, external_api_tool])

"""Security and tool factories for AgentEngine.

Extracted from ``agent_engine.py`` to keep that module within the
800-line limit.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING

from synthorg.approval.protocol import ApprovalStoreProtocol

# Concrete ``CostTracker`` (not ``CostTrackerProtocol``): this factory
# threads the tracker into ``security`` components (``LlmSecurityEvaluator``,
# ``SafetyClassifier``, ``UncertaintyChecker``) whose constructors are typed
# against the concrete class, so the boundary here stays concrete.
from synthorg.budget.tracker import CostTracker
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
from synthorg.security.rules.path_traversal_detector import (
    PathTraversalDetector,
)
from synthorg.security.rules.policy_validator import PolicyValidator
from synthorg.security.service import SecOpsService
from synthorg.tools.external_api._runtime import ExternalApiRuntime
from synthorg.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderConfig
    from synthorg.core.effective_autonomy import EffectiveAutonomy

logger = get_logger(__name__)


def make_security_interceptor(  # noqa: PLR0913
    security_config: SecurityConfig | None,
    audit_log: AuditLog,
    *,
    approval_store: ApprovalStoreProtocol | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
    provider_registry: ProviderRegistry | None = None,
    provider_configs: Mapping[str, ProviderConfig] | None = None,
    model_resolver: ModelResolver | None = None,
    cost_tracker: CostTracker | None = None,
) -> SecurityInterceptionStrategy | None:
    """Build the SecOps security interceptor if configured.

    Args:
        security_config: Security configuration, or ``None`` to skip.
        audit_log: Audit log for security events.
        approval_store: Optional approval store for escalation items.
        effective_autonomy: Optional autonomy level override.
        provider_registry: Optional provider registry for LLM-based
            features (safety classifier, uncertainty checker, LLM
            fallback evaluator).
        provider_configs: Provider config dict for family lookup.
        model_resolver: Optional model resolver for multi-provider
            uncertainty checks.
        cost_tracker: Optional cost tracker.  Threaded into the
            UncertaintyChecker so cross-provider uncertainty calls
            emit ``CostRecord``s through the provider chokepoint
            instead of silently bypassing the cost-recording layer.

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
    # available. Both halves are narrowed together inside the ``else`` so
    # every constructor below sees non-``None`` provider infrastructure.
    llm_evaluator = None
    safety_classifier = None
    denial_tracker = None
    uncertainty_checker = None

    if provider_registry is None or provider_configs is None:
        # Warn when LLM-based features are configured but providers are
        # not available -- the features will be silently disabled.
        _warn_disabled_features(cfg)
    else:
        if cfg.llm_fallback.enabled:
            from synthorg.security.llm_evaluator import (  # noqa: PLC0415
                LlmSecurityEvaluator,
            )

            llm_evaluator = LlmSecurityEvaluator(
                provider_registry=provider_registry,
                provider_configs=provider_configs,
                config=cfg.llm_fallback,
            )

        if cfg.safety_classifier.enabled:
            from synthorg.security.denial_tracker import (  # noqa: PLC0415
                DenialTracker,
            )
            from synthorg.security.safety_classifier import (  # noqa: PLC0415
                SafetyClassifier,
            )

            safety_classifier = SafetyClassifier(
                provider_registry=provider_registry,
                provider_configs=provider_configs,
                config=cfg.safety_classifier,
            )
            denial_tracker = DenialTracker(
                max_consecutive=cfg.safety_classifier.max_consecutive_denials,
                max_total=cfg.safety_classifier.max_total_denials,
            )

        if model_resolver is not None and cfg.uncertainty_check.enabled:
            from synthorg.security.uncertainty import (  # noqa: PLC0415
                UncertaintyChecker,
            )

            uncertainty_checker = UncertaintyChecker(
                provider_registry=provider_registry,
                model_resolver=model_resolver,
                config=cfg.uncertainty_check,
                cost_tracker=cost_tracker,
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
    from synthorg.tools.registry import (  # noqa: PLC0415
        ToolRegistry as _ToolRegistry,
    )

    approval_tool = RequestHumanApprovalTool(
        approval_store=approval_store,
        risk_classifier=default_risk_classifier(miss_event=TIMEOUT_UNKNOWN_ACTION_TYPE),
        agent_id=str(identity.id),
        task_id=task_id,
    )
    existing = list(tool_registry.all_tools())
    return _ToolRegistry([*existing, approval_tool])


def registry_with_external_api_tool(  # noqa: PLR0913 -- run-scoped wiring inputs
    tool_registry: ToolRegistry,
    runtime: ExternalApiRuntime | None,
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

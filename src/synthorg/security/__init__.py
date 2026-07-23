"""Security subsystem: SecOps agent, rule engine, audit, and models.

Public API:

- ``SecOpsService``: the meta-agent coordinating security.
- ``SecurityConfig``: top-level security configuration.
- ``SecurityVerdict`` / ``SecurityVerdictType``: evaluation results.
- ``SecurityContext``: tool invocation context for evaluation.
- ``AuditEntry`` / ``AuditLog``: audit recording.
- ``OutputScanResult`` / ``ScanOutcome`` / ``OutputScanner``:
  post-tool output scanning.
- ``OutputScanResponsePolicy``: protocol for output scan policies.
- ``RedactPolicy`` / ``WithholdPolicy`` / ``LogOnlyPolicy``
  / ``AutonomyTieredPolicy``: policy implementations.
- ``OutputScanPolicyType`` / ``build_output_scan_policy``:
  config-driven policy selection.
- ``SecurityInterceptionStrategy``: protocol for the ToolInvoker.
- ``ActionTypeRegistry`` / ``ActionTypeCategory``: action taxonomy.
- ``RuleEngine`` / ``SecurityRule``: rule evaluation.
- ``CustomPolicyRule``: user-defined policy rule wrapper.
"""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.security.action_types import (
        ActionTypeCategory,
        ActionTypeRegistry,
    )
    from synthorg.security.audit import AuditLog
    from synthorg.security.config import (
        ArgumentTruncationStrategy,
        LlmFallbackConfig,
        LlmFallbackErrorPolicy,
        OutputScanPolicyType,
        RuleEngineConfig,
        SafetyClassifierConfig,
        SecurityConfig,
        SecurityEnforcementMode,
        SecurityPolicyRule,
        UncertaintyCheckConfig,
        VerdictReasonVisibility,
    )
    from synthorg.security.denial_tracker import DenialAction, DenialTracker
    from synthorg.security.information_stripper import InformationStripper
    from synthorg.security.models import (
        AuditEntry,
        EvaluationConfidence,
        OutputScanResult,
        ScanOutcome,
        SecurityContext,
        SecurityVerdict,
        SecurityVerdictType,
    )
    from synthorg.security.output_scan_policy import (
        AutonomyTieredPolicy,
        LogOnlyPolicy,
        OutputScanResponsePolicy,
        RedactPolicy,
        WithholdPolicy,
    )
    from synthorg.security.output_scan_policy_factory import build_output_scan_policy
    from synthorg.security.output_scanner import OutputScanner
    from synthorg.security.protocol import SecurityInterceptionStrategy
    from synthorg.security.risk_scorer import (
        DefaultRiskScorer,
        RiskScore,
        RiskScorer,
        RiskScorerWeights,
    )
    from synthorg.security.rules.custom_policy_rule import CustomPolicyRule
    from synthorg.security.rules.engine import RuleEngine
    from synthorg.security.rules.protocol import SecurityRule
    from synthorg.security.safety_classifier import (
        PermissionTier,
        SafetyClassification,
        SafetyClassifier,
        SafetyClassifierResult,
    )
    from synthorg.security.service import SecOpsService
    from synthorg.security.uncertainty import UncertaintyChecker, UncertaintyResult

# name -> (module path, attribute) for PEP 562 lazy resolution. The eager
# re-exports pulled ``security.uncertainty`` -> ``engine.prompt_safety`` -> the
# ``engine`` hub at package import, so importing a light leaf such as
# ``security.autonomy.enums`` dragged the whole engine graph in and closed a
# cross-package cycle (ADR-0012). Resolving on first access keeps
# ``from synthorg.security import SecOpsService`` working unchanged.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "ActionTypeCategory": ("synthorg.security.action_types", "ActionTypeCategory"),
    "ActionTypeRegistry": ("synthorg.security.action_types", "ActionTypeRegistry"),
    "AuditLog": ("synthorg.security.audit", "AuditLog"),
    "ArgumentTruncationStrategy": (
        "synthorg.security.config",
        "ArgumentTruncationStrategy",
    ),
    "LlmFallbackConfig": ("synthorg.security.config", "LlmFallbackConfig"),
    "LlmFallbackErrorPolicy": ("synthorg.security.config", "LlmFallbackErrorPolicy"),
    "OutputScanPolicyType": ("synthorg.security.config", "OutputScanPolicyType"),
    "RuleEngineConfig": ("synthorg.security.config", "RuleEngineConfig"),
    "SafetyClassifierConfig": ("synthorg.security.config", "SafetyClassifierConfig"),
    "SecurityConfig": ("synthorg.security.config", "SecurityConfig"),
    "SecurityEnforcementMode": (
        "synthorg.security.config",
        "SecurityEnforcementMode",
    ),
    "SecurityPolicyRule": ("synthorg.security.config", "SecurityPolicyRule"),
    "UncertaintyCheckConfig": ("synthorg.security.config", "UncertaintyCheckConfig"),
    "VerdictReasonVisibility": (
        "synthorg.security.config",
        "VerdictReasonVisibility",
    ),
    "DenialAction": ("synthorg.security.denial_tracker", "DenialAction"),
    "DenialTracker": ("synthorg.security.denial_tracker", "DenialTracker"),
    "InformationStripper": (
        "synthorg.security.information_stripper",
        "InformationStripper",
    ),
    "AuditEntry": ("synthorg.security.models", "AuditEntry"),
    "EvaluationConfidence": ("synthorg.security.models", "EvaluationConfidence"),
    "OutputScanResult": ("synthorg.security.models", "OutputScanResult"),
    "ScanOutcome": ("synthorg.security.models", "ScanOutcome"),
    "SecurityContext": ("synthorg.security.models", "SecurityContext"),
    "SecurityVerdict": ("synthorg.security.models", "SecurityVerdict"),
    "SecurityVerdictType": ("synthorg.security.models", "SecurityVerdictType"),
    "AutonomyTieredPolicy": (
        "synthorg.security.output_scan_policy",
        "AutonomyTieredPolicy",
    ),
    "LogOnlyPolicy": ("synthorg.security.output_scan_policy", "LogOnlyPolicy"),
    "OutputScanResponsePolicy": (
        "synthorg.security.output_scan_policy",
        "OutputScanResponsePolicy",
    ),
    "RedactPolicy": ("synthorg.security.output_scan_policy", "RedactPolicy"),
    "WithholdPolicy": ("synthorg.security.output_scan_policy", "WithholdPolicy"),
    "build_output_scan_policy": (
        "synthorg.security.output_scan_policy_factory",
        "build_output_scan_policy",
    ),
    "OutputScanner": ("synthorg.security.output_scanner", "OutputScanner"),
    "SecurityInterceptionStrategy": (
        "synthorg.security.protocol",
        "SecurityInterceptionStrategy",
    ),
    "DefaultRiskScorer": ("synthorg.security.risk_scorer", "DefaultRiskScorer"),
    "RiskScore": ("synthorg.security.risk_scorer", "RiskScore"),
    "RiskScorer": ("synthorg.security.risk_scorer", "RiskScorer"),
    "RiskScorerWeights": ("synthorg.security.risk_scorer", "RiskScorerWeights"),
    "CustomPolicyRule": (
        "synthorg.security.rules.custom_policy_rule",
        "CustomPolicyRule",
    ),
    "RuleEngine": ("synthorg.security.rules.engine", "RuleEngine"),
    "SecurityRule": ("synthorg.security.rules.protocol", "SecurityRule"),
    "PermissionTier": ("synthorg.security.safety_classifier", "PermissionTier"),
    "SafetyClassification": (
        "synthorg.security.safety_classifier",
        "SafetyClassification",
    ),
    "SafetyClassifier": ("synthorg.security.safety_classifier", "SafetyClassifier"),
    "SafetyClassifierResult": (
        "synthorg.security.safety_classifier",
        "SafetyClassifierResult",
    ),
    "SecOpsService": ("synthorg.security.service", "SecOpsService"),
    "UncertaintyChecker": ("synthorg.security.uncertainty", "UncertaintyChecker"),
    "UncertaintyResult": ("synthorg.security.uncertainty", "UncertaintyResult"),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    with _LAZY_EXPORT_LOCK:
        if name in globals():
            return globals()[name]
        module_path, attr = _LAZY_EXPORTS[name]
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = [
    "ActionTypeCategory",
    "ActionTypeRegistry",
    "ArgumentTruncationStrategy",
    "AuditEntry",
    "AuditLog",
    "AutonomyTieredPolicy",
    "CustomPolicyRule",
    "DefaultRiskScorer",
    "DenialAction",
    "DenialTracker",
    "EvaluationConfidence",
    "InformationStripper",
    "LlmFallbackConfig",
    "LlmFallbackErrorPolicy",
    "LogOnlyPolicy",
    "OutputScanPolicyType",
    "OutputScanResponsePolicy",
    "OutputScanResult",
    "OutputScanner",
    "PermissionTier",
    "RedactPolicy",
    "RiskScore",
    "RiskScorer",
    "RiskScorerWeights",
    "RuleEngine",
    "RuleEngineConfig",
    "SafetyClassification",
    "SafetyClassifier",
    "SafetyClassifierConfig",
    "SafetyClassifierResult",
    "ScanOutcome",
    "SecOpsService",
    "SecurityConfig",
    "SecurityContext",
    "SecurityEnforcementMode",
    "SecurityInterceptionStrategy",
    "SecurityPolicyRule",
    "SecurityRule",
    "SecurityVerdict",
    "SecurityVerdictType",
    "UncertaintyCheckConfig",
    "UncertaintyChecker",
    "UncertaintyResult",
    "VerdictReasonVisibility",
    "WithholdPolicy",
    "build_output_scan_policy",
]

"""Security subsystem -- SecOps agent, rule engine, audit, and models.

Public API:

- ``SecOpsService`` -- the meta-agent coordinating security.
- ``SecurityConfig`` -- top-level security configuration.
- ``SecurityVerdict`` / ``SecurityVerdictType`` -- evaluation results.
- ``SecurityContext`` -- tool invocation context for evaluation.
- ``AuditEntry`` / ``AuditLog`` -- audit recording.
- ``OutputScanResult`` / ``ScanOutcome`` / ``OutputScanner``:
  post-tool output scanning.
- ``OutputScanResponsePolicy`` -- protocol for output scan policies.
- ``RedactPolicy`` / ``WithholdPolicy`` / ``LogOnlyPolicy``
  / ``AutonomyTieredPolicy`` -- policy implementations.
- ``OutputScanPolicyType`` / ``build_output_scan_policy``:
  config-driven policy selection.
- ``SecurityInterceptionStrategy`` -- protocol for the ToolInvoker.
- ``ActionTypeRegistry`` / ``ActionTypeCategory`` -- action taxonomy.
- ``RuleEngine`` / ``SecurityRule`` -- rule evaluation.
- ``CustomPolicyRule`` -- user-defined policy rule wrapper.
"""

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
from synthorg.security.output_scan_policy_factory import (
    build_output_scan_policy,
)
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
    InformationStripper,
    PermissionTier,
    SafetyClassification,
    SafetyClassifier,
    SafetyClassifierResult,
)
from synthorg.security.service import SecOpsService
from synthorg.security.uncertainty import (
    UncertaintyChecker,
    UncertaintyResult,
)

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

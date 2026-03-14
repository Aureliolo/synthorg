"""Security subsystem — SecOps agent, rule engine, audit, and models.

Public API:

- ``SecOpsService`` — the meta-agent coordinating security.
- ``SecurityConfig`` — top-level security configuration.
- ``SecurityVerdict`` / ``SecurityVerdictType`` — evaluation results.
- ``SecurityContext`` — tool invocation context for evaluation.
- ``AuditEntry`` / ``AuditLog`` — audit recording.
- ``OutputScanResult`` / ``ScanOutcome`` / ``OutputScanner``
  — post-tool output scanning.
- ``OutputScanResponsePolicy`` — protocol for output scan policies.
- ``RedactPolicy`` / ``WithholdPolicy`` / ``LogOnlyPolicy``
  / ``AutonomyTieredPolicy`` — policy implementations.
- ``OutputScanPolicyType`` / ``build_output_scan_policy`` —
  config-driven policy selection.
- ``SecurityInterceptionStrategy`` — protocol for the ToolInvoker.
- ``ActionTypeRegistry`` / ``ActionTypeCategory`` — action taxonomy.
- ``RuleEngine`` / ``SecurityRule`` — rule evaluation.
"""

from synthorg.security.action_types import (
    ActionTypeCategory,
    ActionTypeRegistry,
)
from synthorg.security.audit import AuditLog
from synthorg.security.config import (
    OutputScanPolicyType,
    RuleEngineConfig,
    SecurityConfig,
    SecurityPolicyRule,
)
from synthorg.security.models import (
    AuditEntry,
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
from synthorg.security.rules.engine import RuleEngine
from synthorg.security.rules.protocol import SecurityRule
from synthorg.security.service import SecOpsService

__all__ = [
    "ActionTypeCategory",
    "ActionTypeRegistry",
    "AuditEntry",
    "AuditLog",
    "AutonomyTieredPolicy",
    "LogOnlyPolicy",
    "OutputScanPolicyType",
    "OutputScanResponsePolicy",
    "OutputScanResult",
    "OutputScanner",
    "RedactPolicy",
    "RuleEngine",
    "RuleEngineConfig",
    "ScanOutcome",
    "SecOpsService",
    "SecurityConfig",
    "SecurityContext",
    "SecurityInterceptionStrategy",
    "SecurityPolicyRule",
    "SecurityRule",
    "SecurityVerdict",
    "SecurityVerdictType",
    "WithholdPolicy",
    "build_output_scan_policy",
]

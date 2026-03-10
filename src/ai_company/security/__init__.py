"""Security subsystem — SecOps agent, rule engine, audit, and models.

Public API:

- ``SecOpsService`` — the meta-agent coordinating security.
- ``SecurityConfig`` — top-level security configuration.
- ``SecurityVerdict`` / ``SecurityVerdictType`` — evaluation results.
- ``SecurityContext`` — tool invocation context for evaluation.
- ``AuditEntry`` / ``AuditLog`` — audit recording.
- ``OutputScanResult`` / ``OutputScanner`` — post-tool output scanning.
- ``SecurityInterceptionStrategy`` — protocol for the ToolInvoker.
- ``ActionTypeRegistry`` / ``ActionTypeCategory`` — action taxonomy.
- ``RuleEngine`` / ``SecurityRule`` — rule evaluation.
"""

from ai_company.security.action_types import (
    ActionTypeCategory,
    ActionTypeRegistry,
)
from ai_company.security.audit import AuditLog
from ai_company.security.config import (
    RuleEngineConfig,
    SecurityConfig,
    SecurityPolicyRule,
)
from ai_company.security.models import (
    AuditEntry,
    OutputScanResult,
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from ai_company.security.output_scanner import OutputScanner
from ai_company.security.protocol import SecurityInterceptionStrategy
from ai_company.security.rules.engine import RuleEngine
from ai_company.security.rules.protocol import SecurityRule
from ai_company.security.service import SecOpsService

__all__ = [
    "ActionTypeCategory",
    "ActionTypeRegistry",
    "AuditEntry",
    "AuditLog",
    "OutputScanResult",
    "OutputScanner",
    "RuleEngine",
    "RuleEngineConfig",
    "SecOpsService",
    "SecurityConfig",
    "SecurityContext",
    "SecurityInterceptionStrategy",
    "SecurityPolicyRule",
    "SecurityRule",
    "SecurityVerdict",
    "SecurityVerdictType",
]

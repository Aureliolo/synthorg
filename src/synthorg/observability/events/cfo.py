"""CFO / CostOptimizer event constants."""

from typing import Final

CFO_OPTIMIZER_CREATED: Final[str] = "cfo.optimizer.created"
CFO_ANOMALY_DETECTED: Final[str] = "cfo.anomaly.detected"
CFO_ANOMALY_SCAN_COMPLETE: Final[str] = "cfo.anomaly.scan_complete"
CFO_EFFICIENCY_ANALYSIS_COMPLETE: Final[str] = "cfo.efficiency.analysis_complete"
CFO_DOWNGRADE_RECOMMENDED: Final[str] = "cfo.downgrade.recommended"
CFO_DOWNGRADE_SKIPPED: Final[str] = "cfo.downgrade.skipped"
CFO_APPROVAL_EVALUATED: Final[str] = "cfo.approval.evaluated"
CFO_OPERATION_DENIED: Final[str] = "cfo.operation.denied"
CFO_REPORT_GENERATED: Final[str] = "cfo.report.generated"
CFO_REPORT_GENERATOR_CREATED: Final[str] = "cfo.report_generator.created"
CFO_RESOLVER_MISSING: Final[str] = "cfo.resolver.missing"
CFO_INSUFFICIENT_WINDOWS: Final[str] = "cfo.anomaly.insufficient_windows"
CFO_ROUTING_OPTIMIZATION_COMPLETE: Final[str] = "cfo.routing.optimization_complete"
CFO_REPORT_VALIDATION_ERROR: Final[str] = "cfo.report.validation_error"

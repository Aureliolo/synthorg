"""CFO / CostOptimizer event constants."""

from typing import Final

CFO_OPTIMIZER_CREATED: Final[str] = "cfo.optimizer.created"
CFO_ANOMALY_DETECTED: Final[str] = "cfo.anomaly.detected"
CFO_ANOMALY_SCAN_COMPLETE: Final[str] = "cfo.anomaly.scan_complete"
CFO_EFFICIENCY_ANALYSIS_COMPLETE: Final[str] = "cfo.efficiency.analysis_complete"
CFO_DOWNGRADE_RECOMMENDED: Final[str] = "cfo.downgrade.recommended"
CFO_APPROVAL_EVALUATED: Final[str] = "cfo.approval.evaluated"
CFO_OPERATION_DENIED: Final[str] = "cfo.operation.denied"
CFO_REPORT_GENERATED: Final[str] = "cfo.report.generated"
CFO_RECORDS_QUERIED: Final[str] = "cfo.records.queried"

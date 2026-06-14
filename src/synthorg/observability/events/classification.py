"""Classification pipeline event constants."""

from typing import Final

CLASSIFICATION_START: Final[str] = "classification.start"
CLASSIFICATION_COMPLETE: Final[str] = "classification.complete"
CLASSIFICATION_FINDING: Final[str] = "classification.finding"
CLASSIFICATION_ERROR: Final[str] = "classification.error"
CLASSIFICATION_SKIPPED: Final[str] = "classification.skipped"

# Per-detector lifecycle events
DETECTOR_START: Final[str] = "classification.detector.start"
DETECTOR_COMPLETE: Final[str] = "classification.detector.complete"
DETECTOR_ERROR: Final[str] = "classification.detector.error"

# Semantic detector cost and budget events
DETECTOR_BUDGET_EXHAUSTED: Final[str] = "classification.detector.budget_exhausted"
DETECTOR_COST_INCURRED: Final[str] = "classification.detector.cost_incurred"

# Sink and dedup events
CLASSIFICATION_SINK_ERROR: Final[str] = "classification.sink.error"
CLASSIFICATION_FINDING_DEDUPLICATED: Final[str] = "classification.finding.deduplicated"

# Context loader and detector infrastructure events
CONTEXT_LOADER_ERROR: Final[str] = "classification.context_loader.error"
DETECTOR_TIMEOUT: Final[str] = "classification.detector.timeout"
DETECTOR_PARSE_ERROR: Final[str] = "classification.detector.parse_error"

# Budget tracker validation events
INVALID_BUDGET: Final[str] = "classification.budget.invalid_budget"
INVALID_COST: Final[str] = "classification.budget.invalid_cost"

# Composite detector scope filtering events
DETECTOR_SCOPE_FILTERED: Final[str] = "classification.detector.scope_filtered"
DETECTOR_SCOPE_MISMATCH: Final[str] = "classification.detector.scope_mismatch"

# Notification rate limiting events
NOTIFICATION_RATE_LIMITED: Final[str] = "classification.notification.rate_limited"

# Taxonomy store events (feed the SignalsService error aggregator).
TAXONOMY_STORE_APPENDED: Final[str] = "classification.taxonomy_store.appended"
TAXONOMY_STORE_EVICTED: Final[str] = "classification.taxonomy_store.evicted"
TAXONOMY_STORE_APPEND_FAILED: Final[str] = "classification.taxonomy_store.append_failed"

"""Trust domain enumerations."""

from enum import StrEnum


class TrustStrategyType(StrEnum):
    """Strategy type for progressive trust evaluation."""

    DISABLED = "disabled"
    WEIGHTED = "weighted"
    PER_CATEGORY = "per_category"
    MILESTONE = "milestone"


class TrustChangeReason(StrEnum):
    """Reason for a trust level change."""

    SCORE_THRESHOLD = "score_threshold"
    MILESTONE_ACHIEVED = "milestone_achieved"
    HUMAN_APPROVAL = "human_approval"
    TRUST_DECAY = "trust_decay"
    RE_VERIFICATION_FAILED = "re_verification_failed"
    MANUAL = "manual"
    ERROR_RATE = "error_rate"

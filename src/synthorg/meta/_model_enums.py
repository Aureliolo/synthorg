"""Enumerations for the self-improving company meta-loop.

Altitude, lifecycle status, rollout strategy, evolution mode, rule
severity, code-operation, guard verdict, rollout outcome, and
regression verdict enums shared across the meta-loop models.
"""

from enum import StrEnum


class ProposalAltitude(StrEnum):
    """Altitude of change a proposal targets."""

    CONFIG_TUNING = "config_tuning"
    ARCHITECTURE = "architecture"
    PROMPT_TUNING = "prompt_tuning"
    CODE_MODIFICATION = "code_modification"
    TOOL_CREATION = "tool_creation"


class ProposalStatus(StrEnum):
    """Lifecycle status of an improvement proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLYING = "applying"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    REGRESSED = "regressed"


class RolloutStrategyType(StrEnum):
    """How an approved proposal is deployed."""

    BEFORE_AFTER = "before_after"
    CANARY = "canary"
    AB_TEST = "ab_test"


class EvolutionMode(StrEnum):
    """How prompt tuning proposals interact with agent evolution."""

    ORG_WIDE = "org_wide"
    OVERRIDE = "override"
    ADVISORY = "advisory"


class RuleSeverity(StrEnum):
    """Severity of a rule match."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class CodeOperation(StrEnum):
    """Type of source file change in a code modification proposal."""

    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class GuardVerdict(StrEnum):
    """Outcome of a guard evaluation."""

    PASSED = "passed"
    REJECTED = "rejected"


class RolloutOutcome(StrEnum):
    """Final outcome of a rollout."""

    SUCCESS = "success"
    REGRESSED = "regressed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class RegressionVerdict(StrEnum):
    """Result of a regression check."""

    NO_REGRESSION = "no_regression"
    THRESHOLD_BREACH = "threshold_breach"
    STATISTICAL_REGRESSION = "statistical_regression"
    INSUFFICIENT_DATA = "insufficient_data"

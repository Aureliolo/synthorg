"""Built-in signal rules for the meta-loop.

Each rule is a class implementing the SignalRule protocol.
Rules detect specific patterns in OrgSignalSnapshot data
and return a RuleMatch when the pattern is found.

All thresholds are configurable via constructor arguments
with sensible defaults.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgSignalSnapshot,
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.meta.protocol import SignalRule

logger = get_logger(__name__)

_DEFAULT_QUALITY_DECLINING_THRESHOLD: Final[float] = 5.0
_DEFAULT_SUCCESS_RATE_DROP_THRESHOLD: Final[float] = 0.7
_DEFAULT_BUDGET_OVERRUN_DAYS_THRESHOLD: Final[int] = 14
_DEFAULT_COORDINATION_COST_RATIO_THRESHOLD: Final[float] = 0.4
_DEFAULT_COORDINATION_OVERHEAD_THRESHOLD_PCT: Final[float] = 35.0
_DEFAULT_STRAGGLER_BOTTLENECK_THRESHOLD: Final[float] = 2.0
_DEFAULT_REDUNDANCY_THRESHOLD: Final[float] = 0.3
_DEFAULT_SCALING_FAILURE_THRESHOLD: Final[float] = 0.5
_DEFAULT_SCALING_MIN_DECISIONS: Final[int] = 3
_DEFAULT_ERROR_SPIKE_THRESHOLD: Final[int] = 10
# A regression needs a predecessor run to compare against, so the rule
# only fires once at least this many runs are on the curve.
_BENCHMARK_REGRESSION_MIN_RUNS: Final[int] = 2

# ── Performance rules ──────────────────────────────────────────────


class QualityDecliningRule:
    """Fires when org-wide quality score is below threshold.

    Checks if the average quality score across all agents
    has dropped below a configurable threshold.

    Args:
        threshold: Minimum acceptable quality (0-10, default 5.0).
    """

    def __init__(
        self, *, threshold: float = _DEFAULT_QUALITY_DECLINING_THRESHOLD
    ) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("quality_declining")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning, prompt tuning, and code modification.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.PROMPT_TUNING,
            ProposalAltitude.CODE_MODIFICATION,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if quality is below threshold.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        perf = snapshot.performance
        if perf.agent_count == 0:
            return None
        if perf.avg_quality_score < self._threshold:
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Org quality {perf.avg_quality_score:.2f} "
                    f"below threshold {self._threshold:.2f}"
                ),
                signal_context={
                    "avg_quality": perf.avg_quality_score,
                    "threshold": self._threshold,
                    "agent_count": perf.agent_count,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


class SuccessRateDropRule:
    """Fires when org-wide success rate drops below threshold.

    Args:
        threshold: Minimum acceptable success rate (0-1, default 0.7).
    """

    def __init__(
        self, *, threshold: float = _DEFAULT_SUCCESS_RATE_DROP_THRESHOLD
    ) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("success_rate_drop")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning.

        Returns:
            Tuple of the declared element types.
        """
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if success rate is below threshold.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        perf = snapshot.performance
        if perf.agent_count == 0:
            return None
        if perf.avg_success_rate < self._threshold:
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Success rate {perf.avg_success_rate:.2%} "
                    f"below threshold {self._threshold:.2%}"
                ),
                signal_context={
                    "avg_success_rate": perf.avg_success_rate,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


# ── Budget rules ───────────────────────────────────────────────────


class BudgetOverrunRule:
    """Fires when budget exhaustion is imminent.

    Args:
        days_threshold: Warn when fewer than N days remain
            (default 14).
    """

    def __init__(
        self, *, days_threshold: int = _DEFAULT_BUDGET_OVERRUN_DAYS_THRESHOLD
    ) -> None:
        self._days_threshold = days_threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("budget_overrun")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning.

        Returns:
            Tuple of the declared element types.
        """
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if budget will be exhausted soon.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        budget = snapshot.budget
        if (
            budget.days_until_exhausted is not None
            and budget.days_until_exhausted <= self._days_threshold
        ):
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.CRITICAL,
                description=(
                    f"Budget exhaustion in "
                    f"{budget.days_until_exhausted} days "
                    f"(threshold: {self._days_threshold})"
                ),
                signal_context={
                    "days_until_exhausted": budget.days_until_exhausted,
                    "threshold": self._days_threshold,
                    "total_spend": budget.total_spend,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


class CoordinationCostRatioRule:
    """Fires when coordination spend exceeds threshold.

    Args:
        threshold: Max acceptable coordination ratio (0-1, default 0.4).
    """

    def __init__(
        self, *, threshold: float = _DEFAULT_COORDINATION_COST_RATIO_THRESHOLD
    ) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("coordination_cost_ratio")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning and architecture changes.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if coordination costs are too high.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        budget = snapshot.budget
        if budget.coordination_ratio > self._threshold:
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Coordination cost ratio "
                    f"{budget.coordination_ratio:.1%} "
                    f"exceeds threshold {self._threshold:.1%}"
                ),
                signal_context={
                    "coordination_ratio": budget.coordination_ratio,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


# ── Coordination rules ─────────────────────────────────────────────


class CoordinationOverheadRule:
    """Fires when coordination overhead percentage is too high.

    Args:
        threshold: Max acceptable overhead % (default 35.0).
    """

    def __init__(
        self, *, threshold: float = _DEFAULT_COORDINATION_OVERHEAD_THRESHOLD_PCT
    ) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("coordination_overhead")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning.

        Returns:
            Tuple of the declared element types.
        """
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if coordination overhead is too high.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        coord = snapshot.coordination
        if (
            coord.coordination_overhead_pct is not None
            and coord.coordination_overhead_pct > self._threshold
        ):
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Coordination overhead "
                    f"{coord.coordination_overhead_pct:.1f}% "
                    f"exceeds threshold {self._threshold:.1f}%"
                ),
                signal_context={
                    "overhead_pct": coord.coordination_overhead_pct,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


class StragglerBottleneckRule:
    """Fires when straggler gap ratio is consistently high.

    Args:
        threshold: Max acceptable straggler ratio (default 2.0).
    """

    def __init__(
        self, *, threshold: float = _DEFAULT_STRAGGLER_BOTTLENECK_THRESHOLD
    ) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("straggler_bottleneck")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning and architecture changes.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.ARCHITECTURE,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if straggler gap is too large.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        coord = snapshot.coordination
        if (
            coord.straggler_gap_ratio is not None
            and coord.straggler_gap_ratio > self._threshold
        ):
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.INFO,
                description=(
                    f"Straggler gap ratio "
                    f"{coord.straggler_gap_ratio:.2f} "
                    f"exceeds threshold {self._threshold:.2f}"
                ),
                signal_context={
                    "straggler_gap_ratio": coord.straggler_gap_ratio,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


class RedundancyRule:
    """Fires when work redundancy rate is too high.

    Args:
        threshold: Max acceptable redundancy rate (0-1, default 0.3).
    """

    def __init__(self, *, threshold: float = _DEFAULT_REDUNDANCY_THRESHOLD) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("redundancy")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning.

        Returns:
            Tuple of the declared element types.
        """
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if redundancy rate is too high.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        coord = snapshot.coordination
        if (
            coord.redundancy_rate is not None
            and coord.redundancy_rate > self._threshold
        ):
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.INFO,
                description=(
                    f"Redundancy rate "
                    f"{coord.redundancy_rate:.2f} "
                    f"exceeds threshold {self._threshold:.2f}"
                ),
                signal_context={
                    "redundancy_rate": coord.redundancy_rate,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


# ── Scaling rules ──────────────────────────────────────────────────


class ScalingFailureRule:
    """Fires when scaling decisions have a high failure rate.

    Args:
        threshold: Max acceptable failure ratio (0-1, default 0.5).
        min_decisions: Minimum decisions to evaluate (default 3).
    """

    def __init__(
        self,
        *,
        threshold: float = _DEFAULT_SCALING_FAILURE_THRESHOLD,
        min_decisions: int = _DEFAULT_SCALING_MIN_DECISIONS,
    ) -> None:
        self._threshold = threshold
        self._min_decisions = min_decisions

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("scaling_failure")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning.

        Returns:
            Tuple of the declared element types.
        """
        return (ProposalAltitude.CONFIG_TUNING,)

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if scaling decisions are failing too often.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        scaling = snapshot.scaling
        if scaling.total_decisions < self._min_decisions:
            return None
        failure_rate = 1.0 - scaling.success_rate
        if failure_rate > self._threshold:
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Scaling failure rate "
                    f"{failure_rate:.1%} "
                    f"exceeds threshold {self._threshold:.1%} "
                    f"({scaling.total_decisions} decisions)"
                ),
                signal_context={
                    "failure_rate": failure_rate,
                    "success_rate": scaling.success_rate,
                    "total_decisions": scaling.total_decisions,
                    "threshold": self._threshold,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


# ── Error rules ────────────────────────────────────────────────────


class ErrorSpikeRule:
    """Fires when error findings exceed a threshold.

    Args:
        threshold: Max acceptable total findings (default 10).
    """

    def __init__(self, *, threshold: int = _DEFAULT_ERROR_SPIKE_THRESHOLD) -> None:
        self._threshold = threshold

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("error_spike")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests config tuning, prompt tuning, and code modification.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.CONFIG_TUNING,
            ProposalAltitude.PROMPT_TUNING,
            ProposalAltitude.CODE_MODIFICATION,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check if error count exceeds threshold.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        errors = snapshot.errors
        if errors.total_findings > self._threshold:
            return RuleMatch(
                rule_name=self.name,
                severity=RuleSeverity.WARNING,
                description=(
                    f"Error findings ({errors.total_findings}) "
                    f"exceed threshold ({self._threshold})"
                ),
                signal_context={
                    "total_findings": errors.total_findings,
                    "threshold": self._threshold,
                    "most_severe": errors.most_severe_category,
                },
                suggested_altitudes=self.target_altitudes,
            )
        return None


# ── Benchmark rules ────────────────────────────────────────────────


class BenchmarkRegressionRule:
    """Fires CRITICAL when the latest golden-benchmark run regressed.

    The golden-company benchmark is the org's ground-truth quality
    signal, so a regression -- the latest scored run dropping materially
    below its predecessor -- is the strongest "something got worse"
    signal available and warrants the highest severity. It suggests
    prompt-tuning and code-modification remediations, the altitudes that
    can move a benchmark score back up.
    """

    @property
    def name(self) -> NotBlankStr:
        """Rule name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("benchmark_regression")

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Suggests prompt tuning and code modification.

        Returns:
            Tuple of the declared element types.
        """
        return (
            ProposalAltitude.PROMPT_TUNING,
            ProposalAltitude.CODE_MODIFICATION,
        )

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Check whether the latest benchmark run regressed.

        Returns:
            The ``RuleMatch`` value when present, ``None`` otherwise.
        """
        bench = snapshot.benchmark
        if bench.run_count < _BENCHMARK_REGRESSION_MIN_RUNS:
            return None
        if not bench.is_regression:
            return None
        return RuleMatch(
            rule_name=self.name,
            severity=RuleSeverity.CRITICAL,
            description=(
                f"Benchmark score dropped {abs(bench.delta)} points "
                f"(latest {bench.latest_total}/{bench.max_total})"
            ),
            signal_context={
                "latest_total": bench.latest_total,
                "max_total": bench.max_total,
                "delta": bench.delta,
                "run_count": bench.run_count,
            },
            suggested_altitudes=self.target_altitudes,
        )


# ── Default rule set ───────────────────────────────────────────────


def default_rules() -> tuple[SignalRule, ...]:
    """Create the default set of built-in rules with default thresholds.

    Returns:
        Tuple of all built-in rules.
    """
    return (
        QualityDecliningRule(),
        SuccessRateDropRule(),
        BudgetOverrunRule(),
        CoordinationCostRatioRule(),
        CoordinationOverheadRule(),
        StragglerBottleneckRule(),
        RedundancyRule(),
        ScalingFailureRule(),
        ErrorSpikeRule(),
        BenchmarkRegressionRule(),
    )

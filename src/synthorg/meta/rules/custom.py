"""Custom declarative signal rules for the meta-loop.

Provides a declarative rule format that users can create via the
dashboard without writing Python code.  Each custom rule evaluates
a single snapshot metric against a threshold using a comparator.

The ``METRIC_REGISTRY`` enumerates all available metrics from
``OrgSignalSnapshot`` with metadata for UI rendering (label,
domain, bounds, unit, nullability).
"""

import operator as _operator
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.persistence.custom_rule_protocol import CustomRuleRepository

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.meta.models import (
    OrgSignalSnapshot,
    ProposalAltitude,
    RuleMatch,
    RuleSeverity,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_CUSTOM_RULE_LISTED

logger = get_logger(__name__)


# ── Comparator ────────────────────────────────────────────────────


class Comparator(StrEnum):
    """Comparison operator for threshold evaluation."""

    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    EQ = "eq"
    NE = "ne"

    def to_operator(self) -> Callable[[float | int, float | int], bool]:
        """Return the stdlib ``operator`` function for this comparator."""
        return _COMPARATOR_OPS[self]

    def symbol(self) -> str:
        """Return the human-readable symbol (e.g. ``<``, ``>=``)."""
        return _COMPARATOR_SYMBOLS[self]


_COMPARATOR_OPS = MappingProxyType(
    {
        Comparator.LT: _operator.lt,
        Comparator.LE: _operator.le,
        Comparator.GT: _operator.gt,
        Comparator.GE: _operator.ge,
        Comparator.EQ: _operator.eq,
        Comparator.NE: _operator.ne,
    }
)

_COMPARATOR_SYMBOLS = MappingProxyType(
    {
        Comparator.LT: "<",
        Comparator.LE: "<=",
        Comparator.GT: ">",
        Comparator.GE: ">=",
        Comparator.EQ: "==",
        Comparator.NE: "!=",
    }
)


# ── MetricDescriptor ─────────────────────────────────────────────


class MetricDescriptor(BaseModel):
    """Metadata for one evaluable metric in OrgSignalSnapshot.

    Attributes:
        path: Dot-notation path into the snapshot (e.g.
            ``performance.avg_quality_score``).
        label: Human-readable display label.
        domain: Signal domain grouping.
        value_type: ``float`` or ``int`` for UI formatting.
        min_value: Known lower bound (for slider), if any.
        max_value: Known upper bound (for slider), if any.
        unit: Display unit (e.g. ``%``, ``USD``, ``days``).
        nullable: Whether the snapshot field can be ``None``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    path: NotBlankStr
    label: NotBlankStr
    domain: NotBlankStr
    value_type: Literal["float", "int"]
    min_value: float | None = None
    max_value: float | None = None
    unit: NotBlankStr | None = None
    nullable: bool = False


# ── Metric registry ──────────────────────────────────────────────


METRIC_REGISTRY: tuple[MetricDescriptor, ...] = (
    # ── Performance ───────────────────────────────────────────
    MetricDescriptor(
        path="performance.avg_quality_score",
        label="Average Quality Score",
        domain="performance",
        value_type="float",
        min_value=0.0,
        max_value=10.0,
    ),
    MetricDescriptor(
        path="performance.avg_success_rate",
        label="Average Success Rate",
        domain="performance",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricDescriptor(
        path="performance.avg_collaboration_score",
        label="Average Collaboration Score",
        domain="performance",
        value_type="float",
        min_value=0.0,
        max_value=10.0,
    ),
    MetricDescriptor(
        path="performance.agent_count",
        label="Active Agent Count",
        domain="performance",
        value_type="int",
        min_value=0.0,
    ),
    # ── Budget ────────────────────────────────────────────────
    MetricDescriptor(
        path="budget.total_spend",
        label="Total Spend",
        domain="budget",
        value_type="float",
        min_value=0.0,
        unit=DEFAULT_CURRENCY,
    ),
    MetricDescriptor(
        path="budget.productive_ratio",
        label="Productive Ratio",
        domain="budget",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricDescriptor(
        path="budget.coordination_ratio",
        label="Coordination Ratio",
        domain="budget",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricDescriptor(
        path="budget.system_ratio",
        label="System Overhead Ratio",
        domain="budget",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricDescriptor(
        path="budget.days_until_exhausted",
        label="Days Until Budget Exhausted",
        domain="budget",
        value_type="int",
        min_value=0.0,
        unit="days",
        nullable=True,
    ),
    MetricDescriptor(
        path="budget.forecast_confidence",
        label="Forecast Confidence",
        domain="budget",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricDescriptor(
        path="budget.orchestration_overhead",
        label="Orchestration Overhead",
        domain="budget",
        value_type="float",
        min_value=0.0,
    ),
    # ── Coordination ──────────────────────────────────────────
    MetricDescriptor(
        path="coordination.coordination_efficiency",
        label="Coordination Efficiency",
        domain="coordination",
        value_type="float",
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.coordination_overhead_pct",
        label="Coordination Overhead %",
        domain="coordination",
        value_type="float",
        unit="%",
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.error_amplification",
        label="Error Amplification",
        domain="coordination",
        value_type="float",
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.message_density",
        label="Message Density",
        domain="coordination",
        value_type="float",
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.redundancy_rate",
        label="Redundancy Rate",
        domain="coordination",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.straggler_gap_ratio",
        label="Straggler Gap Ratio",
        domain="coordination",
        value_type="float",
        nullable=True,
    ),
    MetricDescriptor(
        path="coordination.sample_count",
        label="Coordination Sample Count",
        domain="coordination",
        value_type="int",
        min_value=0.0,
    ),
    # ── Scaling ───────────────────────────────────────────────
    MetricDescriptor(
        path="scaling.total_decisions",
        label="Total Scaling Decisions",
        domain="scaling",
        value_type="int",
        min_value=0.0,
    ),
    MetricDescriptor(
        path="scaling.success_rate",
        label="Scaling Success Rate",
        domain="scaling",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    # ── Errors ────────────────────────────────────────────────
    MetricDescriptor(
        path="errors.total_findings",
        label="Total Error Findings",
        domain="errors",
        value_type="int",
        min_value=0.0,
    ),
    # ── Evolution ─────────────────────────────────────────────
    MetricDescriptor(
        path="evolution.total_proposals",
        label="Total Evolution Proposals",
        domain="evolution",
        value_type="int",
        min_value=0.0,
    ),
    MetricDescriptor(
        path="evolution.approval_rate",
        label="Evolution Approval Rate",
        domain="evolution",
        value_type="float",
        min_value=0.0,
        max_value=1.0,
    ),
    # ── Telemetry ─────────────────────────────────────────────
    MetricDescriptor(
        path="telemetry.event_count",
        label="Total Event Count",
        domain="telemetry",
        value_type="int",
        min_value=0.0,
    ),
    MetricDescriptor(
        path="telemetry.error_event_count",
        label="Error Event Count",
        domain="telemetry",
        value_type="int",
        min_value=0.0,
    ),
)

_VALID_METRIC_PATHS: frozenset[str] = frozenset(m.path for m in METRIC_REGISTRY)


# ── resolve_metric ───────────────────────────────────────────────


def resolve_metric(
    snapshot: OrgSignalSnapshot,
    path: str,
) -> float | int | None:
    """Walk a dot-notation path to extract a metric value.

    Args:
        snapshot: The signal snapshot to read from.
        path: Dot-separated attribute path
            (e.g. ``performance.avg_quality_score``).

    Returns:
        The metric value, or ``None`` for nullable fields.

    Raises:
        AttributeError: If the path does not exist on the snapshot.
    """
    obj: Any = snapshot
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj  # type: ignore[no-any-return]


# ── CustomRuleDefinition ─────────────────────────────────────────


class CustomRuleDefinition(BaseModel):
    """Declarative configuration for a user-defined signal rule.

    Attributes:
        id: Unique rule identifier.
        name: Human-readable rule name (unique across custom rules).
        description: What pattern this rule detects.
        metric_path: Dot-notation path into ``OrgSignalSnapshot``.
        comparator: Comparison operator.
        threshold: Threshold value for comparison.
        severity: Match severity when the rule fires.
        target_altitudes: Which improvement strategies to trigger.
        enabled: Whether the rule is active.
        created_at: When the rule was created.
        updated_at: When the rule was last modified.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: UUID = Field(default_factory=uuid4)
    name: NotBlankStr
    description: NotBlankStr
    metric_path: NotBlankStr
    comparator: Comparator
    threshold: float
    severity: RuleSeverity
    target_altitudes: tuple[ProposalAltitude, ...] = Field(min_length=1)
    enabled: bool = True
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("metric_path")
    @classmethod
    def _validate_metric_path(cls, v: str) -> str:
        if v not in _VALID_METRIC_PATHS:
            valid = ", ".join(sorted(_VALID_METRIC_PATHS))
            msg = (
                f"metric_path '{v}' is not a valid snapshot metric. "
                f"Valid paths: {valid}"
            )
            raise ValueError(msg)
        return v


# ── DeclarativeRule ──────────────────────────────────────────────


class DeclarativeRule:
    """Implements ``SignalRule`` protocol from a ``CustomRuleDefinition``.

    Evaluates a single snapshot metric against a threshold using
    the configured comparator.  For nullable metrics, returns
    ``None`` (no match) when the metric value is absent.

    Args:
        definition: The custom rule configuration.
    """

    def __init__(self, definition: CustomRuleDefinition) -> None:
        self._definition = definition

    @property
    def name(self) -> NotBlankStr:
        """Rule name from the definition."""
        return self._definition.name

    @property
    def target_altitudes(self) -> tuple[ProposalAltitude, ...]:
        """Target altitudes from the definition."""
        return self._definition.target_altitudes

    def evaluate(self, snapshot: OrgSignalSnapshot) -> RuleMatch | None:
        """Evaluate the snapshot against this declarative rule.

        Args:
            snapshot: Current org-wide signal snapshot.

        Returns:
            A ``RuleMatch`` if the condition is met, ``None`` otherwise.
        """
        defn = self._definition
        value = resolve_metric(snapshot, defn.metric_path)

        if value is None:
            return None

        op = defn.comparator.to_operator()
        if op(value, defn.threshold):
            return RuleMatch(
                rule_name=self.name,
                severity=defn.severity,
                description=(
                    f"{defn.metric_path} {defn.comparator.symbol()} "
                    f"{defn.threshold} "
                    f"(current: {value})"
                ),
                signal_context={
                    "metric_path": defn.metric_path,
                    "metric_value": value,
                    "threshold": defn.threshold,
                    "comparator": defn.comparator.value,
                },
                suggested_altitudes=defn.target_altitudes,
            )

        return None


# ── Engine integration ───────────────────────────────────────────


async def load_custom_rules(
    repo: CustomRuleRepository,
) -> tuple[DeclarativeRule, ...]:
    """Load enabled custom rules from persistence as DeclarativeRules.

    Args:
        repo: A ``CustomRuleRepository`` instance.

    Returns:
        Tuple of ``DeclarativeRule`` instances for enabled custom rules.
    """
    definitions = await repo.list_rules(enabled_only=True)
    rules = tuple(DeclarativeRule(d) for d in definitions)
    logger.info(
        META_CUSTOM_RULE_LISTED,
        count=len(rules),
    )
    return rules

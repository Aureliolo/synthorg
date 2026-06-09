"""Request DTOs and serialization helpers for the custom-rules controller.

Holds the create/update/preview request models, the rule and metric
serializers, and the preview-snapshot builder. Kept out of
``custom_rules`` so the controller module stays focused on routing while
the service wiring (``_service`` + ``CustomRulesService``) remains there
for the existing patch target.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    ProposalAltitude,
    RuleSeverity,
)
from synthorg.meta.rules.custom import (
    METRIC_REGISTRY,
    Comparator,
    CustomRuleDefinition,
    MetricDescriptor,
)


class CreateCustomRuleRequest(BaseModel):
    """Request body for creating a custom signal rule.

    Attributes:
        name: Human-readable rule name (unique).
        description: What pattern this rule detects.
        metric_path: Dot-notation path into OrgSignalSnapshot.
        comparator: Comparison operator.
        threshold: Threshold value.
        severity: Match severity.
        target_altitudes: Which strategies to trigger.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(
        description="Human-readable rule name (unique per organization).",
    )
    description: NotBlankStr = Field(
        description="What pattern this rule detects and why it matters.",
    )
    metric_path: NotBlankStr = Field(
        description=(
            "Dot-notation path into `OrgSignalSnapshot` identifying the metric "
            "to evaluate (e.g. `budget.used_percent`)."
        ),
    )
    comparator: Comparator = Field(
        description="Comparison operator (gt, gte, lt, lte, eq, ne).",
    )
    threshold: float = Field(
        description="Threshold value compared against the resolved metric.",
    )
    severity: RuleSeverity = Field(
        description="Severity assigned to matches for downstream routing.",
    )
    target_altitudes: tuple[ProposalAltitude, ...] = Field(
        min_length=1,
        description="Strategies to trigger when the rule matches.",
    )


class UpdateCustomRuleRequest(BaseModel):
    """Request body for updating a custom signal rule.

    All fields are optional (partial update).

    Attributes:
        name: New rule name.
        description: New description.
        metric_path: New metric path.
        comparator: New comparator.
        threshold: New threshold.
        severity: New severity.
        target_altitudes: New target altitudes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr | None = None
    description: NotBlankStr | None = None
    metric_path: NotBlankStr | None = None
    comparator: Comparator | None = None
    threshold: float | None = None
    severity: RuleSeverity | None = None
    target_altitudes: tuple[ProposalAltitude, ...] | None = None


class PreviewRuleRequest(BaseModel):
    """Request body for dry-run rule evaluation.

    Attributes:
        metric_path: Metric to evaluate.
        comparator: Comparison operator.
        threshold: Threshold value.
        sample_value: Metric value to test against.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    metric_path: NotBlankStr
    comparator: Comparator
    threshold: float
    sample_value: float


def rule_to_dict(rule: CustomRuleDefinition) -> dict[str, object]:
    """Serialize a CustomRuleDefinition for API response.

    Returns:
        Mapping with the declared key/value types.
    """
    return {
        "id": str(rule.id),
        "name": rule.name,
        "description": rule.description,
        "metric_path": rule.metric_path,
        "comparator": rule.comparator.value,
        "threshold": rule.threshold,
        "severity": rule.severity.value,
        "target_altitudes": [a.value for a in rule.target_altitudes],
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
        "updated_at": rule.updated_at.isoformat(),
    }


def _metric_to_dict(metric: MetricDescriptor) -> dict[str, object]:
    """Serialize a MetricDescriptor for API response.

    Returns:
        Mapping with the declared key/value types.
    """
    return {
        "path": metric.path,
        "label": metric.label,
        "domain": metric.domain,
        "value_type": metric.value_type,
        "min_value": metric.min_value,
        "max_value": metric.max_value,
        "unit": metric.unit,
        "nullable": metric.nullable,
    }


def _build_preview_snapshot(
    metric_path: str,
    sample_value: float,
) -> OrgSignalSnapshot:
    """Build a minimal OrgSignalSnapshot with one metric set.

    All other fields use safe defaults (zeros/empty).

    Returns:
        ``OrgSignalSnapshot`` instance.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if "." not in metric_path:
        msg = (
            f"metric_path must be dot-notation '<domain>.<field>', got: {metric_path!r}"
        )
        raise ValueError(msg)
    domain, field = metric_path.split(".", maxsplit=1)

    # Convert to int for integer-typed metrics so the injected sample
    # matches the target field's declared type.
    registry_entry = next(
        (m for m in METRIC_REGISTRY if m.path == metric_path),
        None,
    )
    value: float = (
        int(sample_value)
        if registry_entry is not None and registry_entry.value_type == "int"
        else sample_value
    )

    # Build every summary with safe defaults, then inject the sample into
    # the target domain via a validated copy.
    snapshot = OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=0.0,
            avg_success_rate=0.0,
            avg_collaboration_score=0.0,
            agent_count=0,
        ),
        budget=OrgBudgetSummary(
            total_spend=0.0,
            productive_ratio=0.0,
            coordination_ratio=0.0,
            system_ratio=0.0,
            forecast_confidence=0.0,
            orchestration_overhead=0.0,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )
    summaries: dict[str, BaseModel] = {
        "performance": snapshot.performance,
        "budget": snapshot.budget,
        "coordination": snapshot.coordination,
        "scaling": snapshot.scaling,
        "errors": snapshot.errors,
        "evolution": snapshot.evolution,
        "telemetry": snapshot.telemetry,
    }
    target = summaries.get(domain)
    if target is None:
        msg = (
            f"Internal error: metric domain '{domain}' "
            "not handled in preview snapshot builder"
        )
        raise ValueError(msg)
    # Validate the injected value against the target summary's field
    # constraints, then assemble the snapshot via model_copy. Re-validating
    # the *whole* snapshot would choke on computed fields:
    # ``OrgBenchmarkSummary.score_fraction`` is a ``@computed_field`` that
    # ``model_dump()`` emits but ``model_validate()`` rejects under
    # ``extra="forbid"``. Validating only the changed summary (with its
    # computed fields excluded from the round-trip) still enforces the
    # field's range constraints while leaving the other already-valid
    # summaries untouched.
    target_cls = type(target)
    validated_target = target_cls.model_validate(
        {
            **target.model_dump(exclude=set(target_cls.model_computed_fields)),
            field: value,
        },
    )
    return snapshot.model_copy(update={domain: validated_target})

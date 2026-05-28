"""Custom signal rule CRUD controller.

Provides API endpoints for creating, reading, updating, and
deleting user-defined declarative rules, plus a preview endpoint
for dry-run evaluation.
"""

from datetime import UTC, datetime
from typing import Any, Final

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State
from litestar.status_codes import HTTP_204_NO_CONTENT
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ConflictError, NotFoundError
from synthorg.core.persistence_errors import ConstraintViolationError
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
    DeclarativeRule,
    MetricDescriptor,
)
from synthorg.meta.rules.service import CustomRulesService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_RESOURCE_CONFLICT,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.security import (
    SECURITY_CUSTOM_RULE_CREATED,
    SECURITY_CUSTOM_RULE_DELETED,
    SECURITY_CUSTOM_RULE_TOGGLED,
    SECURITY_CUSTOM_RULE_UPDATED,
)
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 50


def _service(state: State) -> CustomRulesService:
    """Build the per-request :class:`CustomRulesService`.

    Returns:
        ``CustomRulesService`` instance.
    """
    return CustomRulesService(repo=persistence_of(state.app_state).custom_rules)


# ── Request DTOs ──────────────────────────────────────────────────


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


# ── Helpers ───────────────────────────────────────────────────────


def rule_to_dict(rule: CustomRuleDefinition) -> dict[str, Any]:
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


def _metric_to_dict(metric: MetricDescriptor) -> dict[str, Any]:
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


# ── Controller ────────────────────────────────────────────────────


class CustomRuleController(Controller):
    """CRUD endpoints for custom declarative signal rules.

    All endpoints are under ``/meta/custom-rules`` (the app router
    adds the ``/api/v1`` prefix).
    """

    path = "/meta/custom-rules"
    tags = ["meta"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    @get("/")
    async def list_rules(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[dict[str, Any]]:
        """List all custom rules (paginated).

        Args:
            state: Application state.
            cursor: Opaque pagination cursor from the previous page;
                ``None`` starts at the beginning.
            limit: Page size.

        Returns:
            Paginated custom rule definitions.
        """
        rules, _total = await _service(state).list_rules()
        entries = tuple(rule_to_dict(r) for r in rules)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, Any]](data=page, pagination=meta)

    @get("/{rule_id:str}")
    async def get_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> ApiResponse[dict[str, Any]]:
        """Get a single custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule.

        Returns:
            The custom rule definition.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        rule = await _service(state).get(rule_id)
        if rule is None:
            msg = f"Custom rule {rule_id} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="custom_rule",
                rule_id=rule_id,
                operation="read",
            )
            raise NotFoundError(msg)
        return ApiResponse[dict[str, Any]](data=rule_to_dict(rule))

    @post(
        "/",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.create", key="user"),
        ],
        status_code=201,
    )
    async def create_rule(
        self,
        state: State,
        data: CreateCustomRuleRequest,
    ) -> ApiResponse[dict[str, Any]]:
        """Create a new custom rule.

        Args:
            state: Litestar application state.
            data: Rule creation request.

        Returns:
            The created rule definition.

        Raises:
            ConflictError: Raised on the corresponding failure path.
        """
        now = datetime.now(UTC)
        definition = CustomRuleDefinition(
            name=data.name,
            description=data.description,
            metric_path=data.metric_path,
            comparator=data.comparator,
            threshold=data.threshold,
            severity=data.severity,
            target_altitudes=data.target_altitudes,
            created_at=now,
            updated_at=now,
        )
        try:
            saved = await _service(state).create(definition)
        except ConstraintViolationError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="custom_rule",
                operation="create",
                name=data.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        # Custom rules define automation triggers (control plane);
        # route the success event through the audit chain via the
        # SECURITY_* prefix so the mutation is signed and chained
        # alongside settings / autonomy changes. ``rule`` is the bare
        # canonical identifier (UUID), mirroring the
        # ``SECURITY_PROVIDER_CREATED`` naming pattern; the human name
        # rides alongside as ``rule_name`` for readability.
        logger.info(
            SECURITY_CUSTOM_RULE_CREATED,
            rule=str(saved.id),
            rule_name=saved.name,
            metric_path=saved.metric_path,
            severity=saved.severity.value,
        )
        return ApiResponse[dict[str, Any]](
            data=rule_to_dict(saved),
        )

    @patch(
        "/{rule_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.update", key="user"),
        ],
    )
    async def update_rule(
        self,
        state: State,
        rule_id: PathId,
        data: UpdateCustomRuleRequest,
    ) -> ApiResponse[dict[str, Any]]:
        """Update an existing custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to update.
            data: Partial update request.

        Returns:
            The updated rule definition.

        Raises:
            ConflictError: Raised on the corresponding failure path.
        """
        # ``CustomRuleNotFoundError`` inherits ``NotFoundError`` so
        # the central handler maps it to 404 directly; the previous
        # controller-level ``raise NotFoundError(str(exc))`` collapsed
        # the type and lost the discriminating envelope.
        try:
            updated = await _service(state).update(
                NotBlankStr(rule_id),
                data.model_dump(exclude_none=True),
            )
        except ConstraintViolationError as exc:
            logger.warning(
                API_RESOURCE_CONFLICT,
                resource="custom_rule",
                operation="update",
                rule_id=rule_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise ConflictError(str(exc)) from exc
        logger.info(
            SECURITY_CUSTOM_RULE_UPDATED,
            rule=rule_id,
            fields_changed=sorted(data.model_dump(exclude_none=True).keys()),
        )
        return ApiResponse[dict[str, Any]](
            data=rule_to_dict(updated),
        )

    @delete(
        "/{rule_id:str}",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.delete", key="user"),
        ],
        status_code=HTTP_204_NO_CONTENT,
    )
    async def delete_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> None:
        """Delete a custom rule.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to delete.
        """
        # ``CustomRuleNotFoundError`` propagates with its inherited
        # ``NotFoundError`` envelope.
        await _service(state).delete(NotBlankStr(rule_id))
        logger.info(
            SECURITY_CUSTOM_RULE_DELETED,
            rule=rule_id,
        )

    @post(
        "/{rule_id:str}/toggle",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("custom_rules.toggle", key="user"),
        ],
    )
    async def toggle_rule(
        self,
        state: State,
        rule_id: PathId,
    ) -> ApiResponse[dict[str, Any]]:
        """Toggle a custom rule's enabled status.

        Args:
            state: Litestar application state.
            rule_id: UUID of the rule to toggle.

        Returns:
            The updated rule definition.
        """
        # ``CustomRuleNotFoundError`` propagates with its inherited
        # ``NotFoundError`` envelope.
        toggled = await _service(state).toggle(NotBlankStr(rule_id))
        logger.info(
            SECURITY_CUSTOM_RULE_TOGGLED,
            rule=rule_id,
            enabled=toggled.enabled,
        )
        return ApiResponse[dict[str, Any]](
            data=rule_to_dict(toggled),
        )

    @get("/metrics")
    async def list_metrics(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> PaginatedResponse[dict[str, Any]]:
        """List available snapshot metrics for rule building (paginated).

        Returns metric descriptors with bounds and metadata. The
        registry is bounded today but the endpoint is paginated for
        uniform shape with the rest of the list surface.

        Returns:
            ``PaginatedResponse[dict[str, Any]]`` instance.
        """
        entries = tuple(_metric_to_dict(m) for m in METRIC_REGISTRY)
        page, meta = paginate_cursor(
            entries,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(state.app_state),
        )
        return PaginatedResponse[dict[str, Any]](data=page, pagination=meta)

    @post(
        "/preview",
        guards=[
            per_op_rate_limit_from_policy("custom_rules.preview", key="user"),
        ],
    )
    async def preview_rule(
        self,
        data: PreviewRuleRequest,
    ) -> ApiResponse[dict[str, Any]]:
        """Dry-run a rule definition against a sample metric value.

        Args:
            data: Preview request with rule definition and sample.

        Returns:
            Whether the rule would fire and the match details.
        """
        now = datetime.now(UTC)
        definition = CustomRuleDefinition(
            name="preview",
            description="Preview rule",
            metric_path=data.metric_path,
            comparator=data.comparator,
            threshold=data.threshold,
            severity=RuleSeverity.INFO,
            target_altitudes=(ProposalAltitude.CONFIG_TUNING,),
            created_at=now,
            updated_at=now,
        )
        rule = DeclarativeRule(definition)

        # Build a snapshot with the sample value injected.
        snapshot = _build_preview_snapshot(
            data.metric_path,
            data.sample_value,
        )
        match = rule.evaluate(snapshot)
        result: dict[str, Any] = {
            "would_fire": match is not None,
            "match": None,
        }
        if match is not None:
            result["match"] = {
                "rule_name": match.rule_name,
                "severity": match.severity.value,
                "description": match.description,
                "signal_context": match.signal_context,
            }
        return ApiResponse[dict[str, Any]](data=result)


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
    domain, field = metric_path.split(".", maxsplit=1)
    perf_kwargs: dict[str, Any] = {
        "avg_quality_score": 0.0,
        "avg_success_rate": 0.0,
        "avg_collaboration_score": 0.0,
        "agent_count": 0,
    }
    budget_kwargs: dict[str, Any] = {
        "total_spend": 0.0,
        "productive_ratio": 0.0,
        "coordination_ratio": 0.0,
        "system_ratio": 0.0,
        "forecast_confidence": 0.0,
        "orchestration_overhead": 0.0,
    }
    coord_kwargs: dict[str, Any] = {}
    scaling_kwargs: dict[str, Any] = {
        "total_decisions": 0,
        "success_rate": 0.0,
    }
    errors_kwargs: dict[str, Any] = {"total_findings": 0}
    evolution_kwargs: dict[str, Any] = {}
    telemetry_kwargs: dict[str, Any] = {}

    # Inject the sample value into the right domain.
    lookup = {
        "performance": perf_kwargs,
        "budget": budget_kwargs,
        "coordination": coord_kwargs,
        "scaling": scaling_kwargs,
        "errors": errors_kwargs,
        "evolution": evolution_kwargs,
        "telemetry": telemetry_kwargs,
    }
    target = lookup.get(domain)
    if target is None:
        msg = (
            f"Internal error: metric domain '{domain}' "
            "not handled in preview snapshot builder"
        )
        raise ValueError(msg)
    # Convert to int for integer fields.
    registry_entry = next(
        (m for m in METRIC_REGISTRY if m.path == metric_path),
        None,
    )
    if registry_entry is not None and registry_entry.value_type == "int":
        target[field] = int(sample_value)
    else:
        target[field] = sample_value

    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(**perf_kwargs),
        budget=OrgBudgetSummary(**budget_kwargs),
        coordination=OrgCoordinationSummary(**coord_kwargs),
        scaling=OrgScalingSummary(**scaling_kwargs),
        errors=OrgErrorSummary(**errors_kwargs),
        evolution=OrgEvolutionSummary(**evolution_kwargs),
        telemetry=OrgTelemetrySummary(**telemetry_kwargs),
    )

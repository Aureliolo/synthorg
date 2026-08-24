"""Typed argument models for simple/medium MCP domains.

Houses args models for the smaller MCP domains where each tool's
shape is too small to justify its own module:

* ``meta`` (6 tools)
* ``budget`` + ``budget_versions`` (5 tools)
* ``analytics`` + ``metrics`` + ``reports`` (8 tools)
* ``coordination`` + ``coordination_metrics`` (2 tools)
* ``quality`` + ``reviews`` (7 tools)
* ``signals`` (8 tools)
* ``approvals`` (5 tools)

Heavy-cardinality domains (``agents``, ``communication``,
``memory``, ``infrastructure``, ``integrations``, ``organization``,
``workflows``) live in their own modules; this file groups what's
left so we don't ship a dozen tiny files.
"""

from typing import Literal, Self

from pydantic import Field, model_validator

from synthorg.approval.enums import ApprovalStatus as ApprovalStatusEnum
from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    IsoDatetimeStr,
    PaginationFields,
    _ArgsBase,
)


def _check_time_window_ordering(since: str | None, until: str | None) -> None:
    """Reject ``since >= until`` on time-window filter args.

    Used by ``MetricsGetHistoryArgs`` and ``CoordinationMetricsListArgs``
    where both ``since`` and ``until`` are optional ``IsoDatetimeStr``
    values (already validated as timezone-aware ISO 8601).  Rejects a
    zero-width or reversed window so the model boundary matches the
    handler-side :func:`resolve_time_window`, which also rejects
    ``since >= until``.  Returns ``None`` on success; callers should
    ``return self`` after invoking this helper from their
    ``model_validator(mode="after")``.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if since is None or until is None:
        return
    start = parse_iso_utc(since)
    end = parse_iso_utc(until)
    if start >= end:
        msg = (
            f"since must be strictly before until; got since={since!r}, until={until!r}"
        )
        raise ValueError(msg)


# ── meta ────────────────────────────────────────────────────────────


class MetaGetConfigArgs(_ArgsBase):
    """Args for ``meta.get_config``: no fields."""


class MetaListRulesArgs(PaginationFields):
    """Args for ``meta.list_rules``."""


class MetaListMcpToolsArgs(_ArgsBase):
    """Args for ``meta.list_mcp_tools``: no fields."""


class MetaGetMcpServerConfigArgs(_ArgsBase):
    """Args for ``meta.get_mcp_server_config``: no fields."""


class MetaTriggerCycleArgs(AdminGuardrailFields):
    """Args for ``meta.trigger_cycle`` (admin op with guardrails)."""


class MetaQueryFeatureMapArgs(_ArgsBase):
    """Args for ``meta.query_feature_map``: optional exact-name filter.

    Attributes:
        name: When set, return only the feature with this exact name; when
            omitted, return the full index.
    """

    name: NotBlankStr | None = Field(
        default=None,
        description="Exact feature name to filter by (omit for full index)",
    )


# ── budget ──────────────────────────────────────────────────────────


class BudgetGetConfigArgs(_ArgsBase):
    """Args for ``budget.get_config``: no fields."""


class BudgetListRecordsArgs(PaginationFields):
    """Args for ``budget.list_records``."""

    agent_id: NotBlankStr | None = Field(default=None, description="Filter by agent")
    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")


class BudgetGetAgentSpendingArgs(_ArgsBase):
    """Args for ``budget.get_agent_spending``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


class BudgetVersionsListArgs(PaginationFields):
    """Args for ``budget_versions.list``."""


class BudgetVersionsGetArgs(_ArgsBase):
    """Args for ``budget_versions.get``."""

    version_num: int = Field(ge=1, description="Version number")


# ── analytics / metrics / reports ───────────────────────────────────


class _SinceOptionalUntilArgs(_ArgsBase):
    """Time-window mixin: ``since`` required, ``until`` optional.

    Handlers resolve a missing ``until`` to ``now`` via
    :func:`synthorg.meta.mcp.handlers.common_args.resolve_time_window`.
    """

    since: IsoDatetimeStr = Field(
        description="Start datetime (ISO 8601, timezone-aware)",
    )
    until: IsoDatetimeStr | None = Field(
        default=None,
        description="End datetime (ISO 8601, timezone-aware); defaults to now",
    )

    @model_validator(mode="after")
    def _since_before_until(self) -> Self:
        """Reject reversed time windows when both bounds are present.

        Returns:
            ``Self`` instance.
        """
        _check_time_window_ordering(self.since, self.until)
        return self


class _SinceRequiredUntilArgs(_ArgsBase):
    """Time-window mixin: both ``since`` and ``until`` required."""

    since: IsoDatetimeStr = Field(
        description="Start datetime (ISO 8601, timezone-aware)",
    )
    until: IsoDatetimeStr = Field(
        description="End datetime (ISO 8601, timezone-aware)",
    )

    @model_validator(mode="after")
    def _since_before_until(self) -> Self:
        """Reject reversed time windows (``since > until``).

        Returns:
            ``Self`` instance.
        """
        _check_time_window_ordering(self.since, self.until)
        return self


class AnalyticsGetOverviewArgs(_SinceOptionalUntilArgs):
    """Args for ``analytics.get_overview``."""


class AnalyticsGetTrendsArgs(_SinceRequiredUntilArgs):
    """Args for ``analytics.get_trends``."""

    metric_names: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Metrics to analyse (omit for all)",
    )


class AnalyticsGetForecastArgs(_SinceRequiredUntilArgs):
    """Args for ``analytics.get_forecast``."""

    horizon_days: int = Field(
        default=30,
        ge=1,
        le=90,
        description="Forecast horizon in days",
    )


class MetricsGetCurrentArgs(_SinceOptionalUntilArgs):
    """Args for ``metrics.get_current``."""

    metric_names: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Metrics to return (omit for all)",
    )


class MetricsGetHistoryArgs(_SinceRequiredUntilArgs):
    """Args for ``metrics.get_history``."""

    metric_names: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Metrics to sample (non-empty)",
    )
    sample_count: int = Field(
        default=8,
        ge=1,
        le=100,
        description="Number of evenly-spaced samples",
    )


class ReportsListArgs(PaginationFields):
    """Args for ``reports.list``."""


class ReportsGetArgs(_ArgsBase):
    """Args for ``reports.get``."""

    report_id: NotBlankStr = Field(description="Report UUID")


class ReportsGenerateArgs(_ArgsBase):
    """Args for ``reports.generate``."""

    template: NotBlankStr = Field(description="Report template name")
    options: dict[str, str] | None = Field(
        default=None,
        description="Template rendering options",
    )


# ── coordination ────────────────────────────────────────────────────


class CoordinationGetTaskMetricsArgs(_ArgsBase):
    """Args for ``coordination.get_task_metrics``."""

    task_id: NotBlankStr = Field(description="Task UUID")


class CoordinationMetricsListArgs(PaginationFields):
    """Args for ``coordination_metrics.list``."""

    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")
    agent_id: NotBlankStr | None = Field(default=None, description="Filter by agent")
    since: IsoDatetimeStr | None = Field(
        default=None, description="Start datetime (ISO 8601, timezone-aware)"
    )
    until: IsoDatetimeStr | None = Field(
        default=None, description="End datetime (ISO 8601, timezone-aware)"
    )

    @model_validator(mode="after")
    def _since_before_until(self) -> Self:
        """Reject reversed time windows (``since > until``).

        Returns:
            ``Self`` instance.
        """
        _check_time_window_ordering(self.since, self.until)
        return self


# ── quality / reviews ───────────────────────────────────────────────


class QualityGetSummaryArgs(_ArgsBase):
    """Args for ``quality.get_summary``: no fields."""


class QualityGetAgentQualityArgs(_ArgsBase):
    """Args for ``quality.get_agent_quality``."""

    agent_id: NotBlankStr = Field(description="Agent ID")


class QualityListScoresArgs(PaginationFields):
    """Args for ``quality.list_scores``."""

    agent_id: NotBlankStr | None = Field(default=None, description="Filter by agent")


class ReviewsListArgs(PaginationFields):
    """Args for ``reviews.list``."""

    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")
    reviewer: NotBlankStr | None = Field(default=None, description="Filter by reviewer")


class ReviewsGetArgs(_ArgsBase):
    """Args for ``reviews.get``."""

    review_id: NotBlankStr = Field(description="Review UUID")


class ReviewsCreateArgs(_ArgsBase):
    """Args for ``reviews.create``."""

    task_id: NotBlankStr = Field(description="Task being reviewed")
    verdict: NotBlankStr = Field(description="Review verdict")
    comments: str | None = Field(default=None, description="Review comments")


class ReviewsUpdateArgs(_ArgsBase):
    """Args for ``reviews.update``."""

    review_id: NotBlankStr = Field(description="Review UUID")
    verdict: NotBlankStr | None = Field(default=None, description="Updated verdict")
    comments: str | None = Field(default=None, description="Updated comments")


# ── signals ─────────────────────────────────────────────────────────


class SignalsGetOrgSnapshotArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_org_snapshot``."""


class SignalsGetPerformanceArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_performance``."""


class SignalsGetBudgetArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_budget``."""


class SignalsGetCoordinationArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_coordination``."""


class SignalsGetErrorPatternsArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_error_patterns``."""


class SignalsGetEvolutionOutcomesArgs(_SinceOptionalUntilArgs):
    """Args for ``signals.get_evolution_outcomes``."""


class SignalsGetProposalsArgs(PaginationFields):
    """Args for ``signals.get_proposals``."""

    status: ApprovalStatusEnum | None = Field(
        default=None,
        description="Filter by approval status",
    )


class SignalsSubmitProposalArgs(AdminGuardrailFields):
    """Args for ``signals.submit_proposal`` (privileged; requires confirm)."""

    proposal: dict[str, object] = Field(description="ImprovementProposal payload")


# ── approvals ───────────────────────────────────────────────────────
#
# ``ApprovalStatus`` and ``RiskLevel`` are the canonical closed-enum
# surfaces for the approval domain.  ``approvals.py`` derives its wire
# schema ``enum`` lists from these via :func:`typing.get_args` so the
# args model and the JSON Schema cannot drift.

ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]
RiskLevel = Literal["low", "medium", "high", "critical"]

RISK_LEVEL_DEFAULT: RiskLevel = "medium"
"""Canonical default for ``approvals.create.risk_level``.

Mirrored into the wire schema in ``approvals.py`` so the args-model
default and the JSON Schema ``default`` stay in lockstep.
"""


class ApprovalsListArgs(PaginationFields):
    """Args for ``approvals.list``."""

    status: ApprovalStatus | None = Field(default=None, description="Filter by status")
    risk_level: RiskLevel | None = Field(
        default=None,
        description="Filter by risk level",
    )
    action_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by action type",
    )


class ApprovalsGetArgs(_ArgsBase):
    """Args for ``approvals.get``."""

    approval_id: NotBlankStr = Field(description="Approval UUID")


class ApprovalsCreateArgs(_ArgsBase):
    """Args for ``approvals.create``.

    ``title`` is optional; when omitted the handler derives it from the
    first 80 characters of ``description``.
    """

    action_type: NotBlankStr = Field(description="Type of action requiring approval")
    title: NotBlankStr | None = Field(
        default=None,
        description="Short summary (defaults to description prefix)",
    )
    description: NotBlankStr = Field(description="Description of the proposed action")
    risk_level: RiskLevel = Field(
        default=RISK_LEVEL_DEFAULT,
        description="Risk level assessment",
    )


class ApprovalsApproveArgs(AdminGuardrailFields):
    """Args for ``approvals.approve`` (destructive).

    The guardrail's ``reason`` is also the decision reason recorded on the
    approval, so there is no separate free-text comment: one field, read in
    both places, cannot drift from itself.
    """

    approval_id: NotBlankStr = Field(description="Approval UUID")


class ApprovalsRejectArgs(AdminGuardrailFields):
    """Args for ``approvals.reject`` (destructive)."""

    approval_id: NotBlankStr = Field(description="Approval UUID")


__all__ = [
    "AnalyticsGetForecastArgs",
    "AnalyticsGetOverviewArgs",
    "AnalyticsGetTrendsArgs",
    "ApprovalStatus",
    "ApprovalsApproveArgs",
    "ApprovalsCreateArgs",
    "ApprovalsGetArgs",
    "ApprovalsListArgs",
    "ApprovalsRejectArgs",
    "BudgetGetAgentSpendingArgs",
    "BudgetGetConfigArgs",
    "BudgetListRecordsArgs",
    "BudgetVersionsGetArgs",
    "BudgetVersionsListArgs",
    "CoordinationGetTaskMetricsArgs",
    "CoordinationMetricsListArgs",
    "MetaGetConfigArgs",
    "MetaGetMcpServerConfigArgs",
    "MetaListMcpToolsArgs",
    "MetaListRulesArgs",
    "MetaQueryFeatureMapArgs",
    "MetaTriggerCycleArgs",
    "MetricsGetCurrentArgs",
    "MetricsGetHistoryArgs",
    "QualityGetAgentQualityArgs",
    "QualityGetSummaryArgs",
    "QualityListScoresArgs",
    "ReportsGenerateArgs",
    "ReportsGetArgs",
    "ReportsListArgs",
    "ReviewsCreateArgs",
    "ReviewsGetArgs",
    "ReviewsListArgs",
    "ReviewsUpdateArgs",
    "RiskLevel",
    "SignalsGetBudgetArgs",
    "SignalsGetCoordinationArgs",
    "SignalsGetErrorPatternsArgs",
    "SignalsGetEvolutionOutcomesArgs",
    "SignalsGetOrgSnapshotArgs",
    "SignalsGetPerformanceArgs",
    "SignalsGetProposalsArgs",
    "SignalsSubmitProposalArgs",
]

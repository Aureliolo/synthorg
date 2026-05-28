"""Typed argument models for simple/medium MCP domains.

Houses args models for the smaller MCP domains where each tool's
shape is too small to justify its own module:

* ``meta`` (5 tools)
* ``budget`` + ``budget_versions`` (5 tools)
* ``analytics`` + ``metrics`` + ``reports`` (8 tools)
* ``coordination`` + ``coordination_metrics`` + ``scaling`` +
  ``ceremony_policy`` (10 tools)
* ``quality`` + ``reviews`` + ``evaluation_versions`` (9 tools)
* ``signals`` (9 tools)
* ``approvals`` (5 tools)

Heavy-cardinality domains (``agents``, ``communication``,
``memory``, ``infrastructure``, ``integrations``, ``organization``,
``workflows``) live in their own modules; this file groups what's
left so we don't ship a dozen tiny files.
"""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    IsoDatetimeStr,
    PaginationFields,
    _ArgsBase,
)


def _check_time_window_ordering(since: str | None, until: str | None) -> None:
    """Reject ``since > until`` on time-window filter args.

    Used by ``MetricsGetHistoryArgs`` and ``CoordinationMetricsListArgs``
    where both ``since`` and ``until`` are optional ``IsoDatetimeStr``
    values (already validated as timezone-aware ISO 8601).  Returns
    ``None`` on success; callers should ``return self`` after invoking
    this helper from their ``model_validator(mode="after")``.

    Raises:
        ValueError: Raised on the corresponding failure path.
    """
    if since is None or until is None:
        return
    start = datetime.fromisoformat(since)
    end = datetime.fromisoformat(until)
    if start > end:
        msg = f"since must be on or before until; got since={since!r}, until={until!r}"
        raise ValueError(msg)


# ── meta ────────────────────────────────────────────────────────────


class MetaGetConfigArgs(_ArgsBase):
    """Args for ``meta.get_config``: no fields."""


class MetaListRulesArgs(_ArgsBase):
    """Args for ``meta.list_rules``: no fields."""


class MetaListMcpToolsArgs(_ArgsBase):
    """Args for ``meta.list_mcp_tools``: no fields."""


class MetaGetMcpServerConfigArgs(_ArgsBase):
    """Args for ``meta.get_mcp_server_config``: no fields."""


class MetaTriggerCycleArgs(_ArgsBase):
    """Args for ``meta.trigger_cycle``: no fields (admin op, no guardrails)."""


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


AnalyticsTrendPeriod = Literal["daily", "weekly", "monthly"]


class AnalyticsGetOverviewArgs(_ArgsBase):
    """Args for ``analytics.get_overview``: no fields."""


class AnalyticsGetTrendsArgs(_ArgsBase):
    """Args for ``analytics.get_trends``."""

    period: AnalyticsTrendPeriod | None = Field(default=None, description="Time period")
    metric: NotBlankStr | None = Field(default=None, description="Metric to analyze")


class AnalyticsGetForecastArgs(_ArgsBase):
    """Args for ``analytics.get_forecast``."""

    horizon_days: int = Field(
        default=30,
        ge=1,
        le=90,
        description="Forecast horizon in days",
    )


class MetricsGetCurrentArgs(_ArgsBase):
    """Args for ``metrics.get_current``: no fields."""


class MetricsGetHistoryArgs(_ArgsBase):
    """Args for ``metrics.get_history``."""

    metric_name: NotBlankStr | None = Field(default=None, description="Metric name")
    since: IsoDatetimeStr | None = Field(
        default=None,
        description="Start datetime (ISO 8601, timezone-aware)",
    )
    until: IsoDatetimeStr | None = Field(
        default=None,
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


class ReportsListArgs(PaginationFields):
    """Args for ``reports.list``."""


class ReportsGetArgs(_ArgsBase):
    """Args for ``reports.get``."""

    report_id: NotBlankStr = Field(description="Report UUID")


class ReportsGenerateArgs(_ArgsBase):
    """Args for ``reports.generate``."""

    report_type: NotBlankStr = Field(description="Type of report to generate")
    parameters: dict[str, object] = Field(
        default_factory=dict,
        description="Report parameters",
    )


# ── coordination / scaling / ceremony policy ────────────────────────


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


class ScalingListDecisionsArgs(PaginationFields):
    """Args for ``scaling.list_decisions``."""


class ScalingGetDecisionArgs(_ArgsBase):
    """Args for ``scaling.get_decision``."""

    decision_id: NotBlankStr = Field(description="Decision UUID")


class ScalingGetConfigArgs(_ArgsBase):
    """Args for ``scaling.get_config``: no fields."""


class ScalingTriggerArgs(_ArgsBase):
    """Args for ``scaling.trigger``."""

    reason: NotBlankStr = Field(description="Reason for triggering scaling")


class CeremonyPolicyGetArgs(_ArgsBase):
    """Args for ``ceremony_policy.get``: no fields."""


class CeremonyPolicyGetResolvedArgs(_ArgsBase):
    """Args for ``ceremony_policy.get_resolved``."""

    department: NotBlankStr | None = Field(
        default=None,
        description="Department name (optional)",
    )


class CeremonyPolicyGetActiveStrategyArgs(_ArgsBase):
    """Args for ``ceremony_policy.get_active_strategy``: no fields."""


# ── quality / reviews / evaluation_versions ─────────────────────────


class QualityGetSummaryArgs(_ArgsBase):
    """Args for ``quality.get_summary``: no fields."""


class QualityGetAgentQualityArgs(_ArgsBase):
    """Args for ``quality.get_agent_quality``."""

    agent_name: NotBlankStr = Field(description="Agent name")


class QualityListScoresArgs(PaginationFields):
    """Args for ``quality.list_scores``."""

    agent_name: NotBlankStr | None = Field(default=None, description="Filter by agent")


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
    score: float = Field(ge=0, le=1, description="Review score (0-1)")
    feedback: str = Field(default="", description="Review feedback")


class ReviewsUpdateArgs(_ArgsBase):
    """Args for ``reviews.update``."""

    review_id: NotBlankStr = Field(description="Review UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class EvaluationVersionsListArgs(PaginationFields):
    """Args for ``evaluation_versions.list``."""


class EvaluationVersionsGetArgs(_ArgsBase):
    """Args for ``evaluation_versions.get``."""

    version_num: int = Field(ge=1, description="Version number")


# ── signals ─────────────────────────────────────────────────────────


ProposalStatus = Literal["pending", "approved", "applied", "rolled_back", "regressed"]
ProposalTrigger = Literal["manual", "scheduled", "inflection"]


class SignalsWindowDaysArgs(_ArgsBase):
    """Args for signals tools that take a lookback window."""

    window_days: int = Field(default=7, ge=1, description="Lookback window in days")


class SignalsGetOrgSnapshotArgs(SignalsWindowDaysArgs):
    """Args for ``signals.get_org_snapshot``."""


class SignalsGetPerformanceArgs(SignalsWindowDaysArgs):
    """Args for ``signals.get_performance``."""


class SignalsGetBudgetArgs(_ArgsBase):
    """Args for ``signals.get_budget``: no fields."""


class SignalsGetCoordinationArgs(_ArgsBase):
    """Args for ``signals.get_coordination``: no fields."""


class SignalsGetScalingHistoryArgs(_ArgsBase):
    """Args for ``signals.get_scaling_history``: no fields."""


class SignalsGetErrorPatternsArgs(_ArgsBase):
    """Args for ``signals.get_error_patterns``: no fields."""


class SignalsGetEvolutionOutcomesArgs(_ArgsBase):
    """Args for ``signals.get_evolution_outcomes``: no fields."""


class SignalsGetProposalsArgs(_ArgsBase):
    """Args for ``signals.get_proposals``."""

    status: ProposalStatus | None = Field(
        default=None,
        description="Filter by proposal status",
    )


class SignalsSubmitProposalArgs(_ArgsBase):
    """Args for ``signals.submit_proposal``."""

    trigger: ProposalTrigger = Field(
        default="manual",
        description="What triggered this submission",
    )


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
    """Args for ``approvals.create``."""

    action_type: NotBlankStr = Field(description="Type of action requiring approval")
    title: NotBlankStr = Field(description="Short summary of the approval")
    description: NotBlankStr = Field(description="Description of the proposed action")
    risk_level: RiskLevel = Field(
        default=RISK_LEVEL_DEFAULT,
        description="Risk level assessment",
    )


class ApprovalsApproveArgs(_ArgsBase):
    """Args for ``approvals.approve``."""

    approval_id: NotBlankStr = Field(description="Approval UUID")
    comment: str = Field(default="", description="Approval comment")


class ApprovalsRejectArgs(AdminGuardrailFields):
    """Args for ``approvals.reject`` (destructive)."""

    approval_id: NotBlankStr = Field(description="Approval UUID")


__all__ = [
    "AnalyticsGetForecastArgs",
    "AnalyticsGetOverviewArgs",
    "AnalyticsGetTrendsArgs",
    "AnalyticsTrendPeriod",
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
    "CeremonyPolicyGetActiveStrategyArgs",
    "CeremonyPolicyGetArgs",
    "CeremonyPolicyGetResolvedArgs",
    "CoordinationGetTaskMetricsArgs",
    "CoordinationMetricsListArgs",
    "EvaluationVersionsGetArgs",
    "EvaluationVersionsListArgs",
    "MetaGetConfigArgs",
    "MetaGetMcpServerConfigArgs",
    "MetaListMcpToolsArgs",
    "MetaListRulesArgs",
    "MetaTriggerCycleArgs",
    "MetricsGetCurrentArgs",
    "MetricsGetHistoryArgs",
    "ProposalStatus",
    "ProposalTrigger",
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
    "ScalingGetConfigArgs",
    "ScalingGetDecisionArgs",
    "ScalingListDecisionsArgs",
    "ScalingTriggerArgs",
    "SignalsGetBudgetArgs",
    "SignalsGetCoordinationArgs",
    "SignalsGetErrorPatternsArgs",
    "SignalsGetEvolutionOutcomesArgs",
    "SignalsGetOrgSnapshotArgs",
    "SignalsGetPerformanceArgs",
    "SignalsGetProposalsArgs",
    "SignalsGetScalingHistoryArgs",
    "SignalsSubmitProposalArgs",
    "SignalsWindowDaysArgs",
]

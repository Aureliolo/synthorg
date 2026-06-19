"""Smoke tests for the bulk MCP domain args module.

Verifies a representative sample of every domain section
(meta / budget / analytics / coordination / quality / signals /
approvals).  Doesn't test every model individually -- the per-model
shape is exercised when the corresponding handler is migrated to
take typed args.
"""

import pytest
from pydantic import ValidationError

from synthorg.meta.mcp.domains._simple_args import (
    AnalyticsGetForecastArgs,
    AnalyticsGetTrendsArgs,
    ApprovalsCreateArgs,
    ApprovalsListArgs,
    ApprovalsRejectArgs,
    BudgetGetAgentSpendingArgs,
    BudgetListRecordsArgs,
    BudgetVersionsGetArgs,
    CeremonyPolicyGetResolvedArgs,
    MetaTriggerCycleArgs,
    ReportsGenerateArgs,
    ReviewsCreateArgs,
    ScalingTriggerArgs,
    SignalsGetOrgSnapshotArgs,
    SignalsGetProposalsArgs,
    SignalsSubmitProposalArgs,
)

pytestmark = pytest.mark.unit


class TestMetaArgs:
    def test_trigger_cycle_requires_guardrails(self) -> None:
        args = MetaTriggerCycleArgs(confirm=True, reason="manual cycle")
        assert args.confirm is True
        with pytest.raises(ValidationError):
            MetaTriggerCycleArgs.model_validate({})


class TestBudgetArgs:
    def test_list_records_default_pagination(self) -> None:
        args = BudgetListRecordsArgs()
        assert args.offset == 0
        assert args.limit == 50

    def test_get_agent_spending_requires_id(self) -> None:
        with pytest.raises(ValidationError):
            BudgetGetAgentSpendingArgs.model_validate({})

    def test_versions_get_requires_positive_int(self) -> None:
        BudgetVersionsGetArgs(version_num=1)
        with pytest.raises(ValidationError):
            BudgetVersionsGetArgs(version_num=0)


_SINCE = "2026-01-01T00:00:00+00:00"
_UNTIL = "2026-01-02T00:00:00+00:00"


class TestAnalyticsArgs:
    def test_trends_requires_window(self) -> None:
        args = AnalyticsGetTrendsArgs(
            since=_SINCE,
            until=_UNTIL,
            metric_names=("throughput",),
        )
        assert args.metric_names == ("throughput",)
        # ``until`` is required for trends; ``period`` is not part of the
        # contract, so a payload without ``until`` is rejected.
        with pytest.raises(ValidationError):
            AnalyticsGetTrendsArgs.model_validate({"since": _SINCE})
        with pytest.raises(ValidationError):
            AnalyticsGetTrendsArgs.model_validate(
                {"since": _SINCE, "until": _UNTIL, "period": "daily"},
            )

    def test_forecast_horizon_bounds(self) -> None:
        AnalyticsGetForecastArgs(since=_SINCE, until=_UNTIL, horizon_days=1)
        AnalyticsGetForecastArgs(since=_SINCE, until=_UNTIL, horizon_days=90)
        with pytest.raises(ValidationError):
            AnalyticsGetForecastArgs(since=_SINCE, until=_UNTIL, horizon_days=91)

    def test_reports_generate(self) -> None:
        args = ReportsGenerateArgs(template="weekly")
        assert args.options is None


class TestCoordinationArgs:
    def test_scaling_trigger_requires_agent_ids(self) -> None:
        args = ScalingTriggerArgs(agent_ids=("agent-1",))
        assert args.agent_ids == ("agent-1",)
        with pytest.raises(ValidationError):
            ScalingTriggerArgs.model_validate({"agent_ids": []})
        with pytest.raises(ValidationError):
            ScalingTriggerArgs.model_validate({"agent_ids": ["   "]})

    def test_ceremony_resolved_optional_dept(self) -> None:
        args = CeremonyPolicyGetResolvedArgs()
        assert args.department is None


class TestQualityArgs:
    def test_review_create_fields(self) -> None:
        args = ReviewsCreateArgs(task_id="t1", verdict="approve")
        assert args.comments is None
        args_with_comment = ReviewsCreateArgs(
            task_id="t1",
            verdict="approve",
            comments="looks good",
        )
        assert args_with_comment.comments == "looks good"
        with pytest.raises(ValidationError):
            ReviewsCreateArgs.model_validate({"task_id": "t1"})


class TestSignalsArgs:
    def test_snapshot_requires_since_until_optional(self) -> None:
        args = SignalsGetOrgSnapshotArgs(since="2026-01-01T00:00:00+00:00")
        assert args.since == "2026-01-01T00:00:00+00:00"
        assert args.until is None
        with pytest.raises(ValidationError):
            SignalsGetOrgSnapshotArgs.model_validate({})

    def test_proposals_status_closed(self) -> None:
        SignalsGetProposalsArgs.model_validate({"status": "pending"})
        with pytest.raises(ValidationError):
            SignalsGetProposalsArgs.model_validate({"status": "draft"})

    def test_submit_proposal_requires_guardrails_and_proposal(self) -> None:
        args = SignalsSubmitProposalArgs(
            confirm=True,
            reason="ship it",
            proposal={"title": "x"},
        )
        assert args.proposal == {"title": "x"}
        with pytest.raises(ValidationError):
            SignalsSubmitProposalArgs.model_validate({})


class TestApprovalsArgs:
    def test_list_filters(self) -> None:
        args = ApprovalsListArgs(status="pending", risk_level="high")
        assert args.status == "pending"

    def test_create_default_risk(self) -> None:
        args = ApprovalsCreateArgs(
            action_type="deploy",
            title="t",
            description="d",
        )
        assert args.risk_level == "medium"

    def test_reject_requires_destructive_guardrails(self) -> None:
        ApprovalsRejectArgs(
            approval_id="a1",
            confirm=True,
            reason="duplicate request",
        )
        with pytest.raises(ValidationError):
            ApprovalsRejectArgs.model_validate(
                {"approval_id": "a1", "confirm": False, "reason": "x"},
            )
        with pytest.raises(ValidationError):
            ApprovalsRejectArgs.model_validate(
                {"approval_id": "a1", "confirm": True, "reason": "   "},
            )

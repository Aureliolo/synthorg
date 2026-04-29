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


class TestMetaArgs:
    @pytest.mark.unit
    def test_trigger_cycle_no_fields(self) -> None:
        args = MetaTriggerCycleArgs()
        assert args.model_dump() == {}


class TestBudgetArgs:
    @pytest.mark.unit
    def test_list_records_default_pagination(self) -> None:
        args = BudgetListRecordsArgs()
        assert args.offset == 0
        assert args.limit == 50

    @pytest.mark.unit
    def test_get_agent_spending_requires_id(self) -> None:
        with pytest.raises(ValidationError):
            BudgetGetAgentSpendingArgs.model_validate({})

    @pytest.mark.unit
    def test_versions_get_requires_positive_int(self) -> None:
        BudgetVersionsGetArgs(version_num=1)
        with pytest.raises(ValidationError):
            BudgetVersionsGetArgs(version_num=0)


class TestAnalyticsArgs:
    @pytest.mark.unit
    def test_trends_period_is_closed(self) -> None:
        AnalyticsGetTrendsArgs(period="daily")
        with pytest.raises(ValidationError):
            AnalyticsGetTrendsArgs.model_validate({"period": "yearly"})

    @pytest.mark.unit
    def test_forecast_horizon_bounds(self) -> None:
        AnalyticsGetForecastArgs(horizon_days=1)
        AnalyticsGetForecastArgs(horizon_days=90)
        with pytest.raises(ValidationError):
            AnalyticsGetForecastArgs(horizon_days=91)

    @pytest.mark.unit
    def test_reports_generate(self) -> None:
        args = ReportsGenerateArgs(report_type="weekly")
        assert args.parameters == {}


class TestCoordinationArgs:
    @pytest.mark.unit
    def test_scaling_trigger_requires_reason(self) -> None:
        ScalingTriggerArgs(reason="test")
        with pytest.raises(ValidationError):
            ScalingTriggerArgs(reason="   ")

    @pytest.mark.unit
    def test_ceremony_resolved_optional_dept(self) -> None:
        args = CeremonyPolicyGetResolvedArgs()
        assert args.department is None


class TestQualityArgs:
    @pytest.mark.unit
    def test_review_score_bounds(self) -> None:
        ReviewsCreateArgs(task_id="t1", score=0.0)
        ReviewsCreateArgs(task_id="t1", score=1.0)
        with pytest.raises(ValidationError):
            ReviewsCreateArgs(task_id="t1", score=1.1)
        with pytest.raises(ValidationError):
            ReviewsCreateArgs(task_id="t1", score=-0.1)


class TestSignalsArgs:
    @pytest.mark.unit
    def test_window_days_default(self) -> None:
        args = SignalsGetOrgSnapshotArgs()
        assert args.window_days == 7

    @pytest.mark.unit
    def test_proposals_status_closed(self) -> None:
        SignalsGetProposalsArgs(status="pending")
        with pytest.raises(ValidationError):
            SignalsGetProposalsArgs.model_validate({"status": "draft"})

    @pytest.mark.unit
    def test_submit_proposal_default_trigger(self) -> None:
        args = SignalsSubmitProposalArgs()
        assert args.trigger == "manual"


class TestApprovalsArgs:
    @pytest.mark.unit
    def test_list_filters(self) -> None:
        args = ApprovalsListArgs(status="pending", risk_level="high")
        assert args.status == "pending"

    @pytest.mark.unit
    def test_create_default_risk(self) -> None:
        args = ApprovalsCreateArgs(
            action_type="deploy",
            title="t",
            description="d",
        )
        assert args.risk_level == "medium"

    @pytest.mark.unit
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

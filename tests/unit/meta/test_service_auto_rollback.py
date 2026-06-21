"""execute_rollout auto-rolls-back a regressed rollout via the wired executor."""

from unittest.mock import AsyncMock

import pytest

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    ApplyResult,
    ConfigChange,
    ImprovementProposal,
    OrgSignalSnapshot,
    ProposalAltitude,
    ProposalRationale,
    ProposalStatus,
    RegressionResult,
    RegressionVerdict,
    RollbackOperation,
    RollbackPlan,
    RolloutOutcome,
    RolloutResult,
)
from synthorg.meta.protocol import ProposalApplier, RolloutStrategy
from synthorg.meta.rollout.inverse_dispatch import UnknownRollbackOperationError
from synthorg.meta.rollout.regression.composite import TieredRegressionDetector
from synthorg.meta.rollout.rollback import RollbackExecutor
from synthorg.meta.service import SelfImprovementService
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit


def _proposal() -> ImprovementProposal:
    return ImprovementProposal(
        id=as_uuid("prop"),
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="Tune quality weight",
        description="Increase the CI quality weight",
        rationale=ProposalRationale(
            signal_summary="s",
            pattern_detected="p",
            expected_impact="e",
            confidence_reasoning="c",
        ),
        config_changes=(
            ConfigChange(
                path="performance.quality_weight_ci",
                old_value=0.5,
                new_value=0.6,
                description="raise quality weight",
            ),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert_config",
                    target="performance.quality_weight_ci",
                    description="revert",
                ),
            ),
            validation_check="reverted",
        ),
        confidence=0.5,
    )


def _regressed_result(*, with_ops: bool = True) -> RolloutResult:
    ops = (
        RollbackOperation(
            operation_type="revert_config",
            target="performance.quality_weight_ci",
            previous_value=0.5,
            description="revert to 0.5",
        ),
    )
    return RolloutResult(
        proposal_id=as_uuid("prop"),
        outcome=RolloutOutcome.REGRESSED,
        regression_verdict=RegressionVerdict.THRESHOLD_BREACH,
        observation_hours_elapsed=4.0,
        details="Regression detected: threshold_breach on quality",
        applied_rollback_operations=ops if with_ops else (),
    )


def _service(executor: RollbackExecutor | None) -> SelfImprovementService:
    return SelfImprovementService(
        config=SelfImprovementConfig(),
        clock=FakeClock(),
        rollback_executor=executor,
    )


class TestAutoRollbackDispatch:
    async def test_regression_flips_to_rolled_back_on_success(self) -> None:
        executor = mock_of[RollbackExecutor](
            execute_operations=AsyncMock(
                return_value=ApplyResult(success=True, changes_applied=1),
            ),
        )
        svc = _service(executor)

        result = await svc._dispatch_auto_rollback(_regressed_result(), _proposal())

        assert result.outcome is RolloutOutcome.ROLLED_BACK
        # The regression verdict is preserved through the flip.
        assert result.regression_verdict is RegressionVerdict.THRESHOLD_BREACH
        executor.execute_operations.assert_awaited_once()

    async def test_failed_rollback_stays_regressed_with_surfaced_detail(self) -> None:
        executor = mock_of[RollbackExecutor](
            execute_operations=AsyncMock(
                return_value=ApplyResult(
                    success=False,
                    error_message="store offline",
                    changes_applied=0,
                ),
            ),
        )
        svc = _service(executor)

        result = await svc._dispatch_auto_rollback(_regressed_result(), _proposal())

        assert result.outcome is RolloutOutcome.REGRESSED
        assert "auto-rollback failed" in (result.details or "")

    async def test_config_gap_stays_regressed_as_misconfigured(self) -> None:
        # A missing handler is a config gap, not a transient error: it logs
        # ERROR and surfaces a distinct "misconfigured" detail.
        executor = mock_of[RollbackExecutor](
            execute_operations=AsyncMock(
                side_effect=UnknownRollbackOperationError("no handler"),
            ),
        )
        svc = _service(executor)

        result = await svc._dispatch_auto_rollback(_regressed_result(), _proposal())

        assert result.outcome is RolloutOutcome.REGRESSED
        assert "misconfigured" in (result.details or "")

    async def test_unexpected_error_stays_regressed_as_errored(self) -> None:
        executor = mock_of[RollbackExecutor](
            execute_operations=AsyncMock(side_effect=RuntimeError("store exploded")),
        )
        svc = _service(executor)

        result = await svc._dispatch_auto_rollback(_regressed_result(), _proposal())

        assert result.outcome is RolloutOutcome.REGRESSED
        assert "auto-rollback errored" in (result.details or "")

    async def test_no_executor_is_noop(self) -> None:
        svc = _service(None)
        regressed = _regressed_result()

        result = await svc._dispatch_auto_rollback(regressed, _proposal())

        assert result is regressed

    async def test_non_regressed_is_noop(self) -> None:
        executor = mock_of[RollbackExecutor](execute_operations=AsyncMock())
        svc = _service(executor)
        success = _regressed_result().model_copy(
            update={
                "outcome": RolloutOutcome.SUCCESS,
                "regression_verdict": None,
            },
        )

        result = await svc._dispatch_auto_rollback(success, _proposal())

        assert result.outcome is RolloutOutcome.SUCCESS
        executor.execute_operations.assert_not_awaited()

    async def test_regression_without_materialised_ops_is_noop(self) -> None:
        executor = mock_of[RollbackExecutor](execute_operations=AsyncMock())
        svc = _service(executor)

        result = await svc._dispatch_auto_rollback(
            _regressed_result(with_ops=False), _proposal()
        )

        assert result.outcome is RolloutOutcome.REGRESSED
        executor.execute_operations.assert_not_awaited()

    async def test_execute_rollout_chain_dispatches_materialised_ops(self) -> None:
        # Drive the full execute_rollout chain: a SUCCESS rollout carrying
        # applier-materialised ops, flipped to REGRESSED by the detector, must
        # still reach the executor with those ops (proves the ops survive the
        # regression-check model_copy and the auto-rollback dispatch fires).
        ops = (
            RollbackOperation(
                operation_type="revert_config",
                target="performance.quality_weight_ci",
                previous_value=0.5,
                description="revert to 0.5",
            ),
        )
        success = RolloutResult(
            proposal_id=as_uuid("prop"),
            outcome=RolloutOutcome.SUCCESS,
            observation_hours_elapsed=4.0,
            applied_rollback_operations=ops,
        )
        executor = mock_of[RollbackExecutor](
            execute_operations=AsyncMock(
                return_value=ApplyResult(success=True, changes_applied=1),
            ),
        )
        svc = _service(executor)
        svc._rollout_strategies = {
            "before_after": mock_of[RolloutStrategy](
                execute=AsyncMock(return_value=success),
            ),
        }
        svc._appliers = {ProposalAltitude.CONFIG_TUNING: mock_of[ProposalApplier]()}
        svc._detector = mock_of[TieredRegressionDetector](
            check=AsyncMock(
                return_value=RegressionResult(
                    verdict=RegressionVerdict.THRESHOLD_BREACH,
                    breached_metric="quality",
                    baseline_value=8.0,
                    current_value=5.0,
                ),
            ),
        )
        svc._analytics_emitter = None
        approved = _proposal().model_copy(
            update={"status": ProposalStatus.APPROVED},
        )

        result = await svc.execute_rollout(
            approved,
            baseline=mock_of[OrgSignalSnapshot](),
            current=mock_of[OrgSignalSnapshot](),
        )

        assert result.outcome is RolloutOutcome.ROLLED_BACK
        assert result.regression_verdict is RegressionVerdict.THRESHOLD_BREACH
        executor.execute_operations.assert_awaited_once()
        assert executor.execute_operations.call_args.args[0] == ops

"""Unit tests for the pre-flight ForecastGate."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import CostForecaster
from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.engine.pipeline.forecast_gate import ForecastGate
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _config(*, forecast_required: bool = True) -> BudgetConfig:
    return BudgetConfig(forecast_required=forecast_required)


def _fake_now() -> datetime:
    return _NOW


def _work_item(*, forecast_id: UUID | None = None) -> WorkItem:
    return WorkItem(
        origin_adapter_id="test-adapter",
        source=WorkSource.INTAKE,
        title="Build the marketing site",
        raw_intent="A focused brief about the marketing site rebuild.",
        project="marketing",
        requested_by="operator-1",
        priority=Priority.MEDIUM,
        task_type=TaskType.DEVELOPMENT,
        forecast_id=forecast_id,
    )


def _result(work_item: WorkItem) -> WorkPipelineResult:
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id="task-001",
        final_task_status=TaskStatus.COMPLETED,
        phases=(WorkPhaseResult(phase="intake", success=True, duration_seconds=0.01),),
        total_duration_seconds=0.01,
    )


class _StubWorkPipeline:
    """Bare-bones WorkPipeline double used to assert dispatch behavior."""

    def __init__(self) -> None:
        self.calls: list[WorkItem] = []

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        self.calls.append(work_item)
        return _result(work_item)


class _FakeForecastRepo:
    """In-memory CostForecastRepository double for the gate tests."""

    def __init__(self) -> None:
        self.saves: list[Forecast] = []
        self.rows: dict[UUID, Forecast] = {}

    async def save(self, entity: Forecast) -> None:
        self.saves.append(entity)
        self.rows[entity.forecast_id] = entity

    async def get(self, entity_id: UUID) -> Forecast | None:
        return self.rows.get(entity_id)

    async def delete(self, entity_id: UUID) -> bool:
        return self.rows.pop(entity_id, None) is not None

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        ordered = sorted(self.rows.values(), key=lambda f: f.created_at, reverse=True)
        return tuple(ordered[offset : offset + limit])

    async def transition_if(
        self,
        entity_id: UUID,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **_updates: object,
    ) -> bool:
        row = self.rows.get(entity_id)
        if row is None or row.decision is not from_state:
            return False
        self.rows[entity_id] = row.model_copy(update={"decision": to_state})
        return True

    async def query(
        self,
        _filter_spec: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        return await self.list_items(limit=limit, offset=offset)

    async def count(self, _filter_spec: object) -> int:
        return len(self.rows)


def _gate(
    *,
    forecast_required: bool = True,
    repo: _FakeForecastRepo | None = None,
    history: Sequence[float] | None = None,
) -> tuple[ForecastGate, _FakeForecastRepo, _StubWorkPipeline]:
    config = _config(forecast_required=forecast_required)
    history_tuple = tuple(history) if history is not None else ()

    async def lookup(_tier: str, _role_id: str) -> Sequence[float]:
        return history_tuple

    forecaster = CostForecaster(
        budget_config=config,
        history_lookup=lookup,
        clock=_fake_now,
    )

    repo_instance = repo if repo is not None else _FakeForecastRepo()
    work_pipeline = _StubWorkPipeline()
    gate = ForecastGate(
        work_pipeline=work_pipeline,
        forecaster=forecaster,
        forecast_repo=repo_instance,
        budget_config=config,
    )
    return gate, repo_instance, work_pipeline


class TestForecastGate:
    async def test_disabled_passes_through(self) -> None:
        gate, repo, _ = _gate(forecast_required=False)
        result = await gate.run(_work_item())
        assert result.task_id == "task-001"
        assert repo.saves == []

    async def test_missing_forecast_raises_approval_required(self) -> None:
        gate, repo, _ = _gate()
        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item())
        assert info.value.forecast_id is not None
        assert info.value.estimated_cost > 0
        assert info.value.currency == "USD"
        # Fresh row persisted for operator to decide on.
        assert len(repo.saves) == 1
        assert repo.saves[0].decision is ForecastDecision.PENDING

    async def test_pending_forecast_raises_approval_required(self) -> None:
        repo = _FakeForecastRepo()
        existing = Forecast(
            forecast_id=uuid4(),
            brief_hash="a" * 64,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[existing.forecast_id] = existing
        gate, _, _ = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError):
            await gate.run(_work_item(forecast_id=existing.forecast_id))

    async def test_approved_forecast_dispatches(self) -> None:
        repo = _FakeForecastRepo()
        approved = Forecast(
            forecast_id=uuid4(),
            brief_hash="b" * 64,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.APPROVED,
            decided_at=_NOW,
            decided_by="op-1",
            ceiling_amount=1.0,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[approved.forecast_id] = approved
        gate, _, _ = _gate(repo=repo)

        result = await gate.run(_work_item(forecast_id=approved.forecast_id))
        assert result.task_id == "task-001"

    async def test_rejected_forecast_raises_terminal_error(self) -> None:
        repo = _FakeForecastRepo()
        rejected = Forecast(
            forecast_id=uuid4(),
            brief_hash="c" * 64,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.REJECTED,
            decided_at=_NOW,
            decided_by="op-1",
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[rejected.forecast_id] = rejected
        gate, _, _ = _gate(repo=repo)

        with pytest.raises(CostForecastRejectedError) as info:
            await gate.run(_work_item(forecast_id=rejected.forecast_id))
        assert info.value.forecast_id == rejected.forecast_id

    async def test_superseded_forecast_triggers_fresh_estimate(self) -> None:
        repo = _FakeForecastRepo()
        superseded = Forecast(
            forecast_id=uuid4(),
            brief_hash="d" * 64,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.SUPERSEDED,
            decided_at=_NOW,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[superseded.forecast_id] = superseded
        gate, _, _ = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError):
            await gate.run(_work_item(forecast_id=superseded.forecast_id))

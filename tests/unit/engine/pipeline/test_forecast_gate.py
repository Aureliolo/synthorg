"""Unit tests for the pre-flight ForecastGate."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import override
from uuid import UUID, uuid4

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import CostForecaster, compute_brief_hash
from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.engine.pipeline.forecast_gate import ForecastGate, _signal_from_work_item
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _config(*, forecast_required: bool = True) -> BudgetConfig:
    return BudgetConfig(forecast_required=forecast_required)


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


# Hash the standard work item exactly as the gate does so "covering"
# fixtures carry a brief_hash that matches the live work item.
_BRIEF_HASH = compute_brief_hash(
    _signal_from_work_item(_work_item(), currency="USD"),
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
        self.narrator: RunNarrator | None = None

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        self.calls.append(work_item)
        return _result(work_item)

    def attach_narrator(self, narrator: RunNarrator) -> None:
        self.narrator = narrator


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
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        rows = sorted(self.rows.values(), key=lambda f: f.created_at, reverse=True)
        if filter_spec.brief_hash is not None:
            rows = [r for r in rows if r.brief_hash == filter_spec.brief_hash]
        if filter_spec.decision is not None:
            rows = [r for r in rows if r.decision is filter_spec.decision]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=len(self.rows) + 1))


class _RacingForecastRepo(_FakeForecastRepo):
    """Repo double simulating a concurrent pending-row insert.

    The first pending lookup misses (so the gate mints fresh); the save
    then trips the partial-unique index, and the re-query surfaces the
    winning row a concurrent dispatch inserted.
    """

    def __init__(self, winner: Forecast) -> None:
        super().__init__()
        self._winner = winner
        self._save_attempted = False

    @override
    async def save(self, entity: Forecast) -> None:
        self.saves.append(entity)
        self._save_attempted = True
        msg = "duplicate pending forecast"
        raise ConstraintViolationError(
            msg,
            constraint="uq_cost_forecasts_pending_brief",
        )

    @override
    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        if (
            self._save_attempted
            and filter_spec.decision is ForecastDecision.PENDING
            and filter_spec.brief_hash == self._winner.brief_hash
        ):
            return (self._winner,)
        return await super().query(filter_spec, limit=limit, offset=offset)


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
        clock=FakeClock(start=_NOW).now,
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

    async def test_stale_linked_forecast_falls_through_to_fresh(self) -> None:
        """A linked forecast whose brief no longer matches is ignored.

        The existing row's brief_hash differs from the work item's, so
        ``_forecast_covers_brief`` returns False and the gate mints a
        fresh pending forecast (the PENDING-reuse path is covered by
        ``test_pending_forecast_covering_brief_is_reused``)."""
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

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=existing.forecast_id))
        # A fresh row was minted (not the stale linked one reused).
        assert info.value.forecast_id != existing.forecast_id
        assert len(repo.saves) == 1
        assert repo.saves[0].brief_hash == _BRIEF_HASH

    async def test_pending_forecast_covering_brief_is_reused(self) -> None:
        """A pending forecast covering the brief is reused, not re-minted.

        Minting a fresh pending row for the same brief_hash would trip the
        partial-unique index; the gate must re-raise approval-required
        against the existing row instead.
        """
        repo = _FakeForecastRepo()
        existing = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[existing.forecast_id] = existing
        gate, _, work_pipeline = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=existing.forecast_id))
        assert info.value.forecast_id == existing.forecast_id
        # No fresh row minted, no dispatch.
        assert repo.saves == []
        assert work_pipeline.calls == []

    async def test_pending_forecast_for_brief_reused_without_id(self) -> None:
        """A pending row for the brief is reused even when the work item
        carries no forecast_id, so the gate never mints a duplicate that
        would trip the partial-unique index."""
        repo = _FakeForecastRepo()
        existing = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[existing.forecast_id] = existing
        gate, _, work_pipeline = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=None))
        assert info.value.forecast_id == existing.forecast_id
        assert repo.saves == []
        assert work_pipeline.calls == []

    async def test_save_race_reuses_winner_pending_forecast(self) -> None:
        """A concurrent insert that trips the pending-unique index on save
        is recovered by re-reading the winning pending row, not surfaced as
        a ConstraintViolationError."""
        winner = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo = _RacingForecastRepo(winner)
        gate, _, work_pipeline = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=None))
        assert info.value.forecast_id == winner.forecast_id
        assert work_pipeline.calls == []

    async def test_approved_forecast_dispatches(self) -> None:
        repo = _FakeForecastRepo()
        approved = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
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

    async def test_approved_forecast_stamps_ceiling_on_dispatched_item(self) -> None:
        """The approved ceiling rides onto the work item the pipeline runs.

        Guards the intake-phase plumbing: without this the in-loop
        BudgetChecker would only see the global fallback ceiling, never
        the operator-approved per-brief ceiling.
        """
        repo = _FakeForecastRepo()
        approved = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.APPROVED,
            decided_at=_NOW,
            decided_by="op-1",
            ceiling_amount=1.8,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[approved.forecast_id] = approved
        gate, _, work_pipeline = _gate(repo=repo)

        await gate.run(_work_item(forecast_id=approved.forecast_id))

        assert len(work_pipeline.calls) == 1
        dispatched = work_pipeline.calls[0]
        assert dispatched.hard_ceiling == 1.8
        assert dispatched.forecast_id == approved.forecast_id

    async def test_approved_forecast_for_other_brief_is_ignored(self) -> None:
        """A reused forecast_id whose brief_hash no longer matches the
        work item must not carry its stale approval; the gate issues a
        fresh forecast and requires approval instead of dispatching."""
        repo = _FakeForecastRepo()
        stale = Forecast(
            forecast_id=uuid4(),
            brief_hash="z" * 64,
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
        repo.rows[stale.forecast_id] = stale
        gate, _, work_pipeline = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError):
            await gate.run(_work_item(forecast_id=stale.forecast_id))
        assert work_pipeline.calls == []
        assert len(repo.saves) == 1
        assert repo.saves[0].decision is ForecastDecision.PENDING

    async def test_rejected_forecast_raises_terminal_error(self) -> None:
        repo = _FakeForecastRepo()
        rejected = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_HASH,
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

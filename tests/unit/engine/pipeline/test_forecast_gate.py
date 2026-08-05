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
from synthorg.budget.forecast_roles import (
    DEFAULT_ROLE_SKELETON,
    BriefRoleSkeleton,
    RoleSkeletonProvider,
)
from synthorg.budget.forecaster import CostForecaster, compute_brief_hash
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.forecast_gate import ForecastGate, _signal_from_work_item
from synthorg.engine.pipeline.models import (
    WorkItem,
    WorkSource,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec
from tests._shared import FakeClock, StubWorkPipeline

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)


def _config(*, forecast_required: bool = True) -> BudgetConfig:
    return BudgetConfig(forecast_required=forecast_required)


def _work_item(
    *,
    forecast_id: UUID | None = None,
    correlation_id: str = "submission-1",
) -> WorkItem:
    # The correlation id is fixed rather than auto-generated: it is part of
    # the brief digest, so a per-call uuid4 would give every construction a
    # different hash and no fixture could pre-compute one.
    return WorkItem(
        origin_adapter_id="test-adapter",
        source=WorkSource.INTAKE,
        title="Build the marketing site",
        raw_intent="A focused brief about the marketing site rebuild.",
        project="marketing",
        requested_by="operator-1",
        priority=Priority.MEDIUM,
        task_type=TaskType.DEVELOPMENT,
        correlation_id=NotBlankStr(correlation_id),
        forecast_id=forecast_id,
    )


# Hash the standard work item exactly as the gate does so "covering"
# fixtures carry a brief_hash that matches the live work item.
_BRIEF_HASH = compute_brief_hash(
    _signal_from_work_item(
        _work_item(), currency="USD", skeleton=DEFAULT_ROLE_SKELETON
    ),
)
# The same brief with no submission attached: what a forecast asked for on
# its own through ``POST /budget/forecast`` is keyed by, since no work item
# existed when it was generated.
_BRIEF_ONLY_HASH = compute_brief_hash(
    _signal_from_work_item(
        _work_item(), currency="USD", skeleton=DEFAULT_ROLE_SKELETON
    ).model_copy(update={"correlation_id": None}),
)


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

    async def raise_ceiling_if_halted(
        self,
        entity_id: UUID,
        *,
        new_ceiling: float,
        updated_at: datetime,
    ) -> bool:
        row = self.rows.get(entity_id)
        if row is None or row.halt_context is None:
            return False
        self.rows[entity_id] = row.model_copy(
            update={
                "ceiling_amount": new_ceiling,
                "halt_context": None,
                "updated_at": updated_at,
            },
        )
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
        # The partial-unique index rejects a *second* pending row for the
        # brief, not a write to the row already holding the slot. Updating
        # the winner therefore succeeds, which is what lets the gate attach
        # the arriving work item to the row it recovered.
        if entity.forecast_id == self._winner.forecast_id:
            self.rows[entity.forecast_id] = entity
            return
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
    role_skeleton_provider: RoleSkeletonProvider | None = None,
) -> tuple[ForecastGate, _FakeForecastRepo, StubWorkPipeline]:
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
    work_pipeline = StubWorkPipeline()
    gate = ForecastGate(
        work_pipeline=work_pipeline,
        forecaster=forecaster,
        forecast_repo=repo_instance,
        budget_config=config,
        role_skeleton_provider=role_skeleton_provider,
    )
    return gate, repo_instance, work_pipeline


class TestForecastGate:
    async def test_disabled_passes_through(self) -> None:
        gate, repo, _ = _gate(forecast_required=False)
        result = await gate.run(_work_item())
        assert result.task_id == "task-001"
        assert repo.saves == []

    async def test_intake_only_forwards_without_forecast(self) -> None:
        # The intake/continue split rides the conversational path, whose
        # forecast is resolved upstream; the gate forwards verbatim and never
        # persists a forecast row (even with forecasting enabled).
        gate, repo, pipeline = _gate(forecast_required=True)
        work_item = _work_item()
        await gate.intake_only(work_item)
        assert pipeline.calls == [work_item]
        assert repo.saves == []

    async def test_continue_from_intake_forwards_the_spine(self) -> None:
        gate, repo, pipeline = _gate(forecast_required=True)
        work_item = _work_item()
        task = await gate.intake_only(work_item)
        result = await gate.continue_from_intake(work_item, task)
        assert pipeline.continue_calls == [(work_item, task)]
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

    async def test_the_refused_work_item_rides_with_the_forecast(self) -> None:
        """Approval can only run the work if the row kept it."""
        gate, repo, _ = _gate()
        with pytest.raises(CostForecastApprovalRequiredError):
            await gate.run(_work_item())

        stored = repo.saves[0].gated_work_item
        assert stored is not None
        assert stored["title"] == "Build the marketing site"
        assert stored["project"] == "marketing"

    async def test_role_skeleton_provider_widens_the_forecast(self) -> None:
        """A multi-role roster forecasts over real roles, not the placeholder.

        The minted forecast's brief_hash is computed over the provider's role
        skeleton, so it differs from the default single-role hash -- proving the
        live roster (not ``("default",)``) drives the estimate.
        """
        skeleton = BriefRoleSkeleton(
            roles=("Backend Developer", "Designer"),
            model_assignments={
                "Backend Developer": "example-large-001",
                "Designer": "example-small-001",
            },
        )

        async def provider() -> BriefRoleSkeleton:
            return skeleton

        gate, repo, _ = _gate(role_skeleton_provider=provider)
        with pytest.raises(CostForecastApprovalRequiredError):
            await gate.run(_work_item())

        expected_hash = compute_brief_hash(
            _signal_from_work_item(_work_item(), currency="USD", skeleton=skeleton),
        )
        assert repo.saves[0].brief_hash == expected_hash
        assert repo.saves[0].brief_hash != _BRIEF_HASH

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
        against the existing row instead. That row is updated in place to
        hold the work item it now gates, which is a write on the same id
        rather than a second row.
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
        assert [save.forecast_id for save in repo.saves] == [existing.forecast_id]
        assert repo.rows[existing.forecast_id].gated_work_item is not None
        assert work_pipeline.calls == []

    async def test_pending_forecast_for_brief_reused_without_id(self) -> None:
        """A pending row for the brief is reused even when the work item
        carries no forecast_id, so the gate never mints a duplicate that
        would trip the partial-unique index.

        Reused, but not left ungated: the row here holds no work item, and
        approving one that holds none runs nothing, so the gate attaches
        the arriving item before handing the row back."""
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
        assert len(repo.saves) == 1
        attached = repo.saves[0]
        assert attached.forecast_id == existing.forecast_id
        assert attached.gated_work_item is not None
        assert attached.gated_work_item["correlation_id"] == "submission-1"
        assert work_pipeline.calls == []

    async def test_two_submissions_of_one_brief_get_their_own_forecast(self) -> None:
        """Same brief text, two submissions: two rows, each gating its own.

        A shared row would give both callers the same approval request, and
        the redispatcher would then run whichever work item the row happened
        to hold, dropping the other caller's accepted work."""
        repo = _FakeForecastRepo()
        gate, _, _ = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as first:
            await gate.run(_work_item(correlation_id="submission-a"))
        with pytest.raises(CostForecastApprovalRequiredError) as second:
            await gate.run(_work_item(correlation_id="submission-b"))

        assert first.value.forecast_id != second.value.forecast_id
        assert first.value.brief_hash != second.value.brief_hash
        held = [s.gated_work_item for s in repo.saves]
        assert [h["correlation_id"] for h in held if h is not None] == [
            "submission-a",
            "submission-b",
        ]

    async def test_estimate_only_row_is_not_reused_by_a_submission(self) -> None:
        """An estimate asked for on its own gates nothing and stays unreused.

        ``POST /budget/forecast`` persists a row with no work item. If a
        later submission of the same brief could reuse it, approving that
        estimate would release work the approver never saw attached."""
        repo = _FakeForecastRepo()
        estimate_only = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_ONLY_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[estimate_only.forecast_id] = estimate_only
        gate, _, _ = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=None))

        assert info.value.forecast_id != estimate_only.forecast_id
        assert repo.saves[0].gated_work_item is not None

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
        # The recovered row still has to gate this submission, or the
        # caller's 202 names an approval that would run nothing.
        assert repo.rows[winner.forecast_id].gated_work_item is not None
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

    async def test_a_linked_pending_estimate_gains_the_work_it_gates(self) -> None:
        """Naming a standalone estimate makes it gate the arriving work.

        The row was minted with no work item, so approving it as it stands
        would dispatch nothing: the brief the caller was told is awaiting
        approval would be dropped at the approval instead of at the door.
        """
        repo = _FakeForecastRepo()
        estimate_only = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_ONLY_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.PENDING,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[estimate_only.forecast_id] = estimate_only
        gate, _, work_pipeline = _gate(repo=repo)

        with pytest.raises(CostForecastApprovalRequiredError) as info:
            await gate.run(_work_item(forecast_id=estimate_only.forecast_id))

        assert info.value.forecast_id == estimate_only.forecast_id
        held = repo.rows[estimate_only.forecast_id].gated_work_item
        assert held is not None
        assert held["correlation_id"] == "submission-1"
        assert work_pipeline.calls == []

    async def test_approved_estimate_linked_by_id_still_covers_the_brief(
        self,
    ) -> None:
        """An estimate approved before any submission releases the work.

        The operator asks for a standalone estimate, approves it, then
        submits the brief naming that forecast. The row was keyed before a
        submission existed, so its digest carries no correlation id, while
        the arriving work item's does. Only the brief drifting should
        supersede an approval, and it has not drifted here.
        """
        repo = _FakeForecastRepo()
        approved = Forecast(
            forecast_id=uuid4(),
            brief_hash=_BRIEF_ONLY_HASH,
            estimated_cost=0.5,
            lower_bound=0.3,
            upper_bound=0.7,
            currency="USD",
            decision=ForecastDecision.APPROVED,
            decided_at=_NOW,
            decided_by="op-1",
            ceiling_amount=2.5,
            created_at=_NOW,
            updated_at=_NOW,
        )
        repo.rows[approved.forecast_id] = approved
        gate, _, work_pipeline = _gate(repo=repo)

        await gate.run(_work_item(forecast_id=approved.forecast_id))

        assert len(work_pipeline.calls) == 1
        assert work_pipeline.calls[0].hard_ceiling == 2.5

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

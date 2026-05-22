"""Unit tests for the charter approval-to-spine dispatcher."""

from datetime import UTC, datetime
from typing import cast

import pytest

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.enums import CharterStatus, ProjectStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.errors import WorkProjectNotFoundError
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.meta.charter.dispatch import CharterDispatcher
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.meta.errors import CharterAlreadyDecidedError, CharterNotFoundError
from synthorg.persistence.charter_protocol import CharterRepository
from synthorg.persistence.conversation_protocol import ConversationRepository
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import FakeClock

pytestmark = pytest.mark.unit

_START = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)
_CURRENCY = "USD"


def _charter(**overrides: object) -> ProjectCharter:
    defaults: dict[str, object] = {
        "id": "charter-1",
        "conversation_id": "conv-1",
        "created_by": "user-1",
        "title": "Memory layer",
        "brief": "Build a better memory layer.",
        "success_criteria": (NotBlankStr("recall +10%"),),
        "scope": ScopeBoundaries(in_scope=(NotBlankStr("retrieval"),)),
        "envelope": BudgetEnvelope(amount=5000.0, currency=_CURRENCY),
        "proposed_project_name": "memory-layer",
        "created_at": _START,
        "updated_at": _START,
    }
    defaults.update(overrides)
    return ProjectCharter(**defaults)  # type: ignore[arg-type]


class _FakeCharterRepo:
    def __init__(self, charter: ProjectCharter) -> None:
        self.items: dict[str, ProjectCharter] = {charter.id: charter}

    async def get(self, entity_id: str) -> ProjectCharter | None:
        return self.items.get(entity_id)

    async def save(self, entity: ProjectCharter) -> None:
        self.items[entity.id] = entity

    async def transition_if(
        self,
        entity_id: str,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        patch: dict[str, object] = {"status": to_state}
        for key in (
            "approved_at",
            "approved_by",
            "forecast_id",
            "correlation_id",
            "task_id",
        ):
            if key in updates:
                patch[key] = updates[key]
        self.items[entity_id] = current.model_copy(update=patch)
        return True


class _FakeForecastRepo:
    def __init__(self) -> None:
        self.items: dict[str, Forecast] = {}

    async def save(self, entity: Forecast) -> None:
        if entity.currency != _CURRENCY:
            msg = "currency mismatch"
            raise MixedCurrencyAggregationError(
                msg, currencies=frozenset({entity.currency, _CURRENCY})
            )
        self.items[str(entity.forecast_id)] = entity

    async def get(self, entity_id: object) -> Forecast | None:
        return self.items.get(str(entity_id))


class _FakeProjectRepo:
    def __init__(self, existing: dict[str, Project] | None = None) -> None:
        self.items: dict[str, Project] = dict(existing or {})
        self.created: list[Project] = []

    async def get(self, entity_id: str) -> Project | None:
        return self.items.get(entity_id)

    async def create(self, project: Project) -> None:
        self.items[project.id] = project
        self.created.append(project)


class _FakeWorkPipeline:
    def __init__(self) -> None:
        self.ran: list[WorkItem] = []

    async def run(self, work_item: WorkItem) -> object:
        self.ran.append(work_item)
        return SimpleResult(task_id=NotBlankStr("task-1"), is_success=True)


class SimpleResult:
    def __init__(self, *, task_id: NotBlankStr, is_success: bool) -> None:
        self.task_id = task_id
        self.is_success = is_success


class _FakeConversationRepo:
    def __init__(self) -> None:
        self.closed: list[str] = []

    async def transition_if(self, entity_id: str, **kwargs: object) -> bool:
        self.closed.append(entity_id)
        return True


def _dispatcher(
    charter: ProjectCharter,
    *,
    project_repo: _FakeProjectRepo | None = None,
) -> tuple[CharterDispatcher, _FakeForecastRepo, _FakeWorkPipeline, _FakeProjectRepo]:
    from synthorg.api.services.project_service import ProjectService

    charter_repo = _FakeCharterRepo(charter)
    forecast_repo = _FakeForecastRepo()
    proj_repo = project_repo or _FakeProjectRepo()
    pipeline = _FakeWorkPipeline()
    dispatcher = CharterDispatcher(
        charter_repo=cast(CharterRepository, charter_repo),
        forecast_repo=cast(CostForecastRepository, forecast_repo),
        project_service=ProjectService(repo=cast(ProjectRepository, proj_repo)),
        work_pipeline=cast(WorkPipeline, pipeline),
        conversation_repo=cast(ConversationRepository, _FakeConversationRepo()),
        budget_currency=lambda: _CURRENCY,
        clock=FakeClock(start=_START),
    )
    return dispatcher, forecast_repo, pipeline, proj_repo


class TestApprove:
    async def test_new_project_path_creates_project(self) -> None:
        dispatcher, forecast_repo, pipeline, proj_repo = _dispatcher(_charter())
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.project_id == "charter-charter-1"
        assert result.task_id == "task-1"
        assert result.is_success is True
        assert proj_repo.created[0].budget == pytest.approx(5000.0)
        assert proj_repo.created[0].name == "memory-layer"
        assert proj_repo.created[0].status is ProjectStatus.PLANNING
        # Forecast persisted as APPROVED with the envelope ceiling.
        forecast = next(iter(forecast_repo.items.values()))
        assert forecast.decision is ForecastDecision.APPROVED
        assert forecast.ceiling_amount == pytest.approx(5000.0)
        # Work item carries the forecast id, ceiling, and success criteria.
        work_item = pipeline.ran[0]
        assert work_item.forecast_id == forecast.forecast_id
        assert work_item.hard_ceiling == pytest.approx(5000.0)
        assert work_item.acceptance_criteria == ("recall +10%",)
        assert work_item.project == "charter-charter-1"

    async def test_charter_stamped_approved(self) -> None:
        dispatcher, _, _, _ = _dispatcher(_charter())
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.charter.status is CharterStatus.APPROVED
        assert result.charter.approved_by == "user-1"
        assert result.charter.task_id == "task-1"
        assert result.charter.forecast_id is not None

    async def test_existing_project_path(self) -> None:
        existing = Project(id=NotBlankStr("proj-x"), name=NotBlankStr("X"))
        charter = _charter(project_id="proj-x", proposed_project_name=None)
        dispatcher, _, pipeline, proj_repo = _dispatcher(
            charter, project_repo=_FakeProjectRepo({"proj-x": existing})
        )
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.project_id == "proj-x"
        assert proj_repo.created == []
        assert pipeline.ran[0].project == "proj-x"

    async def test_existing_project_missing_raises(self) -> None:
        charter = _charter(project_id="ghost", proposed_project_name=None)
        dispatcher, _, _, _ = _dispatcher(charter)
        with pytest.raises(WorkProjectNotFoundError):
            await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )

    async def test_currency_mismatch_raises_before_project(self) -> None:
        charter = _charter(envelope=BudgetEnvelope(amount=100.0, currency="GBP"))
        dispatcher, _, pipeline, proj_repo = _dispatcher(charter)
        with pytest.raises(MixedCurrencyAggregationError):
            await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )
        assert proj_repo.created == []
        assert pipeline.ran == []

    async def test_double_approve_raises_already_decided(self) -> None:
        dispatcher, _, _, _ = _dispatcher(_charter())
        await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        with pytest.raises(CharterAlreadyDecidedError):
            await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )

    async def test_unknown_charter_raises(self) -> None:
        dispatcher, _, _, _ = _dispatcher(_charter())
        with pytest.raises(CharterNotFoundError):
            await dispatcher.approve(
                NotBlankStr("missing"), approved_by=NotBlankStr("user-1")
            )

    async def test_forecast_id_is_deterministic(self) -> None:
        dispatcher, forecast_repo, _, _ = _dispatcher(_charter())
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        forecast = next(iter(forecast_repo.items.values()))
        # Charter provenance points at the persisted forecast row.
        assert result.charter.forecast_id == forecast.forecast_id

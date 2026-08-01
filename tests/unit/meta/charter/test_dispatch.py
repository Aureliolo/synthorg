"""Unit tests for the charter approval-to-spine dispatcher."""

from datetime import UTC, datetime
from typing import cast, override
from uuid import uuid5

import pytest

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.pipeline.models import PipelineAttachments, WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.meta.charter.dispatch import PROJECT_NAMESPACE, CharterDispatcher
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.meta.errors import CharterAlreadyDecidedError, CharterNotFoundError
from synthorg.observability.events.charter import (
    CHARTER_DISPATCH_FAILED,
    CHARTER_DISPATCH_UNSUCCESSFUL,
    CHARTER_DISPATCHED,
)
from synthorg.persistence.charter_protocol import CharterRepository
from synthorg.persistence.conversation_protocol import ConversationRepository
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import FakeClock, as_uuid, sid
from tests._shared.conversation_fakes import FakeConversationRepo

pytestmark = pytest.mark.unit

_START = datetime(2026, 5, 22, 9, 0, 0, tzinfo=UTC)
_CURRENCY = "USD"
# The dispatcher derives a new project's id deterministically as
# ``uuid5(PROJECT_NAMESPACE, f"charter-{charter.id}")`` so a retried
# approval upserts the same project row.
_EXPECTED_NEW_PROJECT_ID = str(uuid5(PROJECT_NAMESPACE, "charter-charter-1"))


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

    async def save_edit_if_version(
        self,
        entity: ProjectCharter,
        *,
        expected_version: int,
    ) -> bool:
        current = self.items.get(entity.id)
        if (
            current is None
            or current.version != expected_version
            or current.status is not CharterStatus.DRAFTED
        ):
            return False
        self.items[entity.id] = entity
        return True

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
        # Mirror the real repo: stamping project_id clears the proposed name
        # so the existing-vs-new XOR holds after approval.
        project_id = updates.get("project_id")
        if project_id is not None:
            patch["project_id"] = project_id
            patch["proposed_project_name"] = None
        self.items[entity_id] = current.model_copy(update=patch)
        return True

    async def delete(self, entity_id: str) -> bool:
        raise NotImplementedError

    async def list_items(
        self, *, limit: int = 0, offset: int = 0
    ) -> tuple[ProjectCharter, ...]:
        raise NotImplementedError

    async def query(
        self, filter_spec: object, *, limit: int = 0, offset: int = 0
    ) -> tuple[ProjectCharter, ...]:
        raise NotImplementedError

    async def count(self, filter_spec: object) -> int:
        raise NotImplementedError


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

    async def delete(self, entity_id: object) -> bool:
        raise NotImplementedError

    async def list_items(
        self, *, limit: int = 0, offset: int = 0
    ) -> tuple[Forecast, ...]:
        raise NotImplementedError

    async def transition_if(
        self,
        entity_id: object,
        from_state: object,
        to_state: object,
        **updates: object,
    ) -> bool:
        raise NotImplementedError

    async def raise_ceiling_if_halted(
        self,
        entity_id: object,
        *,
        new_ceiling: float,
        updated_at: datetime,
    ) -> bool:
        existing = self.items.get(str(entity_id))
        if existing is None or existing.halt_context is None:
            return False
        self.items[str(entity_id)] = existing.model_copy(
            update={
                "ceiling_amount": new_ceiling,
                "halt_context": None,
                "updated_at": updated_at,
            },
        )
        return True

    async def query(
        self, filter_spec: object, *, limit: int = 0, offset: int = 0
    ) -> tuple[Forecast, ...]:
        raise NotImplementedError

    async def count(self, filter_spec: object) -> int:
        raise NotImplementedError


class _FakeProjectRepo:
    def __init__(self, existing: dict[str, Project] | None = None) -> None:
        self.items: dict[str, Project] = dict(existing or {})
        self.created: list[Project] = []

    async def get(self, entity_id: str) -> Project | None:
        return self.items.get(entity_id)

    async def create(self, project: Project) -> None:
        self.items[str(project.id)] = project
        self.created.append(project)

    async def update(
        self, project: Project, *, expected_version: int | None = None
    ) -> None:
        raise NotImplementedError

    async def save(self, entity: Project) -> None:
        raise NotImplementedError

    async def list_items(
        self, *, limit: int = 0, offset: int = 0
    ) -> tuple[Project, ...]:
        raise NotImplementedError

    async def query(
        self, filter_spec: object, *, limit: int = 0, offset: int = 0
    ) -> tuple[Project, ...]:
        raise NotImplementedError

    async def count(self, filter_spec: object) -> int:
        raise NotImplementedError

    async def delete(self, entity_id: str) -> bool:
        raise NotImplementedError


class _FakeWorkPipeline:
    def __init__(self, *, is_success: bool = True) -> None:
        self.ran: list[WorkItem] = []
        self._is_success = is_success

    async def run(self, work_item: WorkItem) -> object:
        self.ran.append(work_item)
        return SimpleResult(task_id=NotBlankStr("task-1"), is_success=self._is_success)

    async def intake_only(self, work_item: WorkItem) -> object:
        # The charter path drives the batch ``run`` entry, never the split.
        raise NotImplementedError

    async def continue_from_intake(self, work_item: WorkItem, task: object) -> object:
        raise NotImplementedError

    def attach_narrator(self, narrator: object) -> None:
        raise NotImplementedError

    def attach_refinement_router(self, router: object) -> None:
        raise NotImplementedError

    def attach_plan_review_gate(self, gate: object) -> None:
        raise NotImplementedError

    def attach_plan_review_panel(self, panel: object) -> None:
        raise NotImplementedError

    @property
    def attachments(self) -> PipelineAttachments:
        """Report that nothing is attached (the charter path attaches none)."""
        return PipelineAttachments(
            narrator=False,
            refinement_router=False,
            plan_review_gate=False,
            plan_review_panel=False,
        )


class SimpleResult:
    def __init__(self, *, task_id: NotBlankStr, is_success: bool) -> None:
        self.task_id = task_id
        self.is_success = is_success


class _FakeConversationRepo(FakeConversationRepo):
    """Spy over the shared double: records every conversation it closes.

    Subclasses the canonical fake so the repository-protocol surface stays
    in sync automatically; only ``transition_if`` is overridden to record
    the close and always report success (the dispatch tests assert the
    dispatcher attempted the close, not the persistence outcome).
    """

    def __init__(self) -> None:
        super().__init__()
        self.closed: list[str] = []

    @override
    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        self.closed.append(entity_id)
        return True


def _dispatcher(
    charter: ProjectCharter,
    *,
    project_repo: _FakeProjectRepo | None = None,
    pipeline_success: bool = True,
) -> tuple[CharterDispatcher, _FakeForecastRepo, _FakeWorkPipeline, _FakeProjectRepo]:
    charter_repo = _FakeCharterRepo(charter)
    forecast_repo = _FakeForecastRepo()
    proj_repo = project_repo or _FakeProjectRepo()
    pipeline = _FakeWorkPipeline(is_success=pipeline_success)
    dispatcher = CharterDispatcher(
        charter_repo=cast(CharterRepository, charter_repo),
        forecast_repo=cast(CostForecastRepository, forecast_repo),
        project_repo=cast(ProjectRepository, proj_repo),
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
        assert result.project_id == _EXPECTED_NEW_PROJECT_ID
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
        assert work_item.project == _EXPECTED_NEW_PROJECT_ID
        # A charter is an objective: it must always be planned, never run as a
        # single solo leaf, so the spine decomposes it into a plan.
        assert work_item.plan_required is True

    async def test_charter_stamped_approved(self) -> None:
        dispatcher, _, _, _ = _dispatcher(_charter())
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.charter.status is CharterStatus.APPROVED
        assert result.charter.approved_by == "user-1"
        assert result.charter.task_id == "task-1"
        assert result.charter.forecast_id is not None
        # The charter records the project it became; the proposed name is
        # cleared so the run is filed under a concrete project_id.
        assert result.charter.project_id == _EXPECTED_NEW_PROJECT_ID
        assert result.charter.proposed_project_name is None

    async def test_unsuccessful_pipeline_still_approves_but_warns(self) -> None:
        """An empty/failed pipeline run stamps APPROVED yet fails loud.

        The charter transition to APPROVED is correct (a human approved and
        the dispatch happened), but a run that produced no successful work
        surfaces the honest ``is_success=False`` and a WARNING event, never a
        routine ``charter.dispatched`` INFO line that would mask the no-op.
        """
        from structlog.testing import capture_logs

        dispatcher, _, _, _ = _dispatcher(_charter(), pipeline_success=False)
        with capture_logs() as logs:
            result = await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )

        assert result.is_success is False
        assert result.charter.status is CharterStatus.APPROVED
        events = [e.get("event") for e in logs]
        assert CHARTER_DISPATCH_UNSUCCESSFUL in events
        assert CHARTER_DISPATCHED not in events
        unsuccessful = next(
            e for e in logs if e.get("event") == CHARTER_DISPATCH_UNSUCCESSFUL
        )
        assert unsuccessful["log_level"] == "warning"
        assert unsuccessful["is_success"] is False

    async def test_approve_emits_status_transition_log(self) -> None:
        """Approval logs charter.status_transitioned (DRAFTED -> APPROVED)."""
        from structlog.testing import capture_logs

        dispatcher, _, _, _ = _dispatcher(_charter())
        with capture_logs() as logs:
            await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )
        transitions = [
            e for e in logs if e.get("event") == "charter.status_transitioned"
        ]
        assert len(transitions) == 1
        assert transitions[0]["from_state"] == CharterStatus.DRAFTED.value
        assert transitions[0]["to_state"] == CharterStatus.APPROVED.value
        assert transitions[0]["decided_by"] == "user-1"

    async def test_existing_project_path(self) -> None:
        existing = Project(id=as_uuid("proj-x"), name=NotBlankStr("X"))
        charter = _charter(project_id=sid("proj-x"), proposed_project_name=None)
        dispatcher, _, pipeline, proj_repo = _dispatcher(
            charter, project_repo=_FakeProjectRepo({sid("proj-x"): existing})
        )
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.project_id == sid("proj-x")
        assert proj_repo.created == []
        assert pipeline.ran[0].project == sid("proj-x")

    async def test_existing_project_missing_raises(self) -> None:
        charter = _charter(project_id="ghost", proposed_project_name=None)
        dispatcher, _, _, _ = _dispatcher(charter)
        with pytest.raises(ProjectNotFoundError):
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

    async def test_brief_hash_deterministic_for_same_brief(self) -> None:
        # Two charters with the same brief must produce the same forecast
        # brief_hash so a retried approval upserts the same row (the
        # ForecastGate later checks brief_hash to decide coverage).
        dispatcher_a, repo_a, _, _ = _dispatcher(_charter())
        dispatcher_b, repo_b, _, _ = _dispatcher(
            _charter(id="charter-2", conversation_id="conv-2")
        )
        await dispatcher_a.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        await dispatcher_b.approve(
            NotBlankStr("charter-2"), approved_by=NotBlankStr("user-1")
        )
        hash_a = next(iter(repo_a.items.values())).brief_hash
        hash_b = next(iter(repo_b.items.values())).brief_hash
        assert hash_a == hash_b

    async def test_concurrent_approve_only_one_wins_cas(self) -> None:
        import asyncio

        dispatcher, _, pipeline, proj_repo = _dispatcher(_charter())

        async def _approve() -> object:
            try:
                return await dispatcher.approve(
                    NotBlankStr("charter-1"),
                    approved_by=NotBlankStr("user-1"),
                )
            except CharterAlreadyDecidedError as exc:
                return exc

        outcomes = await asyncio.gather(_approve(), _approve())
        # Per-charter lock + status guard yield one success, one
        # already-decided; project / pipeline run only once.
        successes = [o for o in outcomes if not isinstance(o, Exception)]
        already = [o for o in outcomes if isinstance(o, CharterAlreadyDecidedError)]
        assert len(successes) == 1
        assert len(already) == 1
        assert len(pipeline.ran) == 1
        assert len(proj_repo.created) == 1

    async def test_dispatch_failure_is_logged_before_reraise(self) -> None:
        import structlog
        from structlog.testing import capture_logs

        class _BoomPipeline:
            async def run(self, work_item: WorkItem) -> object:
                del work_item
                msg = "spine boom"
                raise RuntimeError(msg)

            async def intake_only(self, work_item: WorkItem) -> object:
                raise NotImplementedError

            async def continue_from_intake(
                self, work_item: WorkItem, task: object
            ) -> object:
                raise NotImplementedError

            @property
            def attachments(self) -> PipelineAttachments:
                return PipelineAttachments(
                    narrator=False,
                    refinement_router=False,
                    plan_review_gate=False,
                    plan_review_panel=False,
                )

            def attach_narrator(self, narrator: object) -> None:
                raise NotImplementedError

            def attach_refinement_router(self, router: object) -> None:
                raise NotImplementedError

            def attach_plan_review_gate(self, gate: object) -> None:
                raise NotImplementedError

            def attach_plan_review_panel(self, panel: object) -> None:
                raise NotImplementedError

        charter_repo = _FakeCharterRepo(_charter())
        forecast_repo = _FakeForecastRepo()
        proj_repo = _FakeProjectRepo()
        dispatcher = CharterDispatcher(
            charter_repo=cast(CharterRepository, charter_repo),
            forecast_repo=cast(CostForecastRepository, forecast_repo),
            project_repo=cast(ProjectRepository, proj_repo),
            work_pipeline=cast(WorkPipeline, _BoomPipeline()),
            conversation_repo=cast(ConversationRepository, _FakeConversationRepo()),
            budget_currency=lambda: _CURRENCY,
            clock=FakeClock(start=_START),
        )
        del structlog  # imported for context; capture_logs is the seam
        with (
            capture_logs() as log_records,
            pytest.raises(RuntimeError, match="spine boom"),
        ):
            await dispatcher.approve(
                NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
            )
        # The failure was structurally logged with the charter id before
        # the exception bubbled, so operators see the dispatch attempt.
        # Pin BOTH the event name AND the structured ``charter_id``: a
        # regression that drops the id key would still pass an
        # event-only check, masking the missing context.
        assert any(
            record.get("event") == CHARTER_DISPATCH_FAILED
            and record.get("charter_id") == "charter-1"
            for record in log_records
        )

    async def test_duplicate_project_branch_is_idempotent(self) -> None:
        # A previous approval attempt created the project; the retry must
        # treat the DuplicateRecordError as a no-op and reuse the project.
        class _DupProjectRepo(_FakeProjectRepo):
            @override
            async def create(self, project: Project) -> None:
                del project
                msg = "duplicate"
                raise DuplicateRecordError(msg)

        charter_repo = _FakeCharterRepo(_charter())
        forecast_repo = _FakeForecastRepo()
        proj_repo = _DupProjectRepo()
        pipeline = _FakeWorkPipeline()
        dispatcher = CharterDispatcher(
            charter_repo=cast(CharterRepository, charter_repo),
            forecast_repo=cast(CostForecastRepository, forecast_repo),
            project_repo=cast(ProjectRepository, proj_repo),
            work_pipeline=cast(WorkPipeline, pipeline),
            conversation_repo=cast(ConversationRepository, _FakeConversationRepo()),
            budget_currency=lambda: _CURRENCY,
            clock=FakeClock(start=_START),
        )
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.project_id == _EXPECTED_NEW_PROJECT_ID
        assert result.is_success is True

    async def test_approve_idempotent_when_conversation_already_closed(self) -> None:
        # The dispatcher's conversation-close path is transition_if(ACTIVE->CLOSED);
        # if the conversation was already CLOSED, the close is a no-op and the
        # approval still succeeds (the spine ran, the charter is APPROVED).
        class _ClosedConvRepo(_FakeConversationRepo):
            @override
            async def transition_if(
                self,
                entity_id: str,
                from_state: ConversationStatus,
                to_state: ConversationStatus,
                **updates: object,
            ) -> bool:
                # Simulate already-closed: transition returns False.
                self.closed.append(entity_id)
                return False

        charter_repo = _FakeCharterRepo(_charter())
        forecast_repo = _FakeForecastRepo()
        proj_repo = _FakeProjectRepo()
        pipeline = _FakeWorkPipeline()
        dispatcher = CharterDispatcher(
            charter_repo=cast(CharterRepository, charter_repo),
            forecast_repo=cast(CostForecastRepository, forecast_repo),
            project_repo=cast(ProjectRepository, proj_repo),
            work_pipeline=cast(WorkPipeline, pipeline),
            conversation_repo=cast(ConversationRepository, _ClosedConvRepo()),
            budget_currency=lambda: _CURRENCY,
            clock=FakeClock(start=_START),
        )
        result = await dispatcher.approve(
            NotBlankStr("charter-1"), approved_by=NotBlankStr("user-1")
        )
        assert result.charter.status is CharterStatus.APPROVED
        assert result.is_success is True

    async def test_approve_by_approval_role_actor_succeeds(self) -> None:
        # Approve is intentionally NOT ownership-fenced at the service
        # layer; the REST surface is gated by `require_approval_roles`
        # and the MCP surface by `require_admin_guardrails`, so an
        # approval-tier actor legitimately dispatches a junior's
        # charter. The original authorship stays on `created_by`.
        dispatcher, _, pipeline, _ = _dispatcher(_charter())
        result = await dispatcher.approve(
            NotBlankStr("charter-1"),
            approved_by=NotBlankStr("ceo-1"),
        )
        assert result.charter.approved_by == "ceo-1"
        assert result.charter.created_by == "user-1"
        assert len(pipeline.ran) == 1

"""Unit tests for :class:`ConversationalPlanDispatcher`.

Covers the seams the proposer suite stubs over: fail-closed dispatch (no port),
project provision-vs-reuse + idempotent re-dispatch, and intake-failure
propagation.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ProposeArgs,
    ProposedWork,
)
from synthorg.meta.chief_of_staff.plan_intake import ConversationalPlanDispatcher
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.settings.resolver import ConfigResolver
from tests._shared import as_uuid, mock_of
from tests._shared.work_pipeline import StubWorkPipeline

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


class _RecordingPort:
    """Records dispatch calls without spawning anything."""

    def __init__(self) -> None:
        self.calls: list[tuple[WorkItem, Task]] = []

    def dispatch_conversational_execution(
        self,
        *,
        work_pipeline: WorkPipeline,
        work_item: WorkItem,
        task: Task,
    ) -> None:
        _ = work_pipeline
        self.calls.append((work_item, task))


def _conversation() -> Conversation:
    return Conversation(
        id=as_uuid("conv-plan"),
        created_by=NotBlankStr("user-1"),
        created_at=_NOW,
        updated_at=_NOW,
        status=ConversationStatus.ACTIVE,
    )


def _args() -> ProposeArgs:
    return ProposeArgs(
        message=NotBlankStr("build the thing"),
        created_by=NotBlankStr("user-1"),
    )


def _work(
    project: str | None = None,
    raw_intent: str = "Build the onboarding flow end to end.",
) -> ProposedWork:
    return ProposedWork(
        title=NotBlankStr("Ship onboarding"),
        raw_intent=NotBlankStr(raw_intent),
        project=NotBlankStr(project) if project else None,
    )


def _project_repo() -> Any:  # type: ignore[explicit-any]  # mock factory; see tests._shared.mock_of
    repo = mock_of[ProjectRepository]()
    repo.get.return_value = None
    repo.create.return_value = None
    return repo


async def test_no_dispatch_port_fails_closed_before_intake() -> None:
    pipeline = StubWorkPipeline()
    dispatcher = ConversationalPlanDispatcher(
        project_repo=_project_repo(),
        work_pipeline=pipeline,
        dispatch_port=None,
    )
    with pytest.raises(ServiceUnavailableError):
        await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
    # No task was created: fail-closed leaves nothing to orphan.
    assert pipeline.calls == []


async def test_happy_path_intakes_and_backgrounds_a_plan_gated_item() -> None:
    pipeline = StubWorkPipeline()
    port = _RecordingPort()
    dispatcher = ConversationalPlanDispatcher(
        project_repo=_project_repo(),
        work_pipeline=pipeline,
        dispatch_port=port,
    )
    summary = await dispatcher.draft_plan(
        conversation=_conversation(), args=_args(), work=_work(), now=_NOW
    )
    assert summary.title == "Ship onboarding"
    assert len(pipeline.calls) == 1
    assert pipeline.calls[0].plan_required is True
    assert len(port.calls) == 1
    dispatched_item, _ = port.calls[0]
    assert dispatched_item.plan_required is True


async def test_named_project_that_exists_is_reused() -> None:
    repo = mock_of[ProjectRepository]()
    repo.get.return_value = Project(
        id=as_uuid("existing"),
        name=NotBlankStr("Existing"),
        status=ProjectStatus.PLANNING,
    )
    dispatcher = ConversationalPlanDispatcher(
        project_repo=repo,
        work_pipeline=StubWorkPipeline(),
        dispatch_port=_RecordingPort(),
    )
    summary = await dispatcher.draft_plan(
        conversation=_conversation(),
        args=_args(),
        work=_work(project="my-project"),
        now=_NOW,
    )
    assert summary.project == "my-project"
    repo.create.assert_not_called()


async def test_absent_project_is_minted_objective_keyed() -> None:
    repo = _project_repo()
    dispatcher = ConversationalPlanDispatcher(
        project_repo=repo,
        work_pipeline=StubWorkPipeline(),
        dispatch_port=_RecordingPort(),
    )
    summary = await dispatcher.draft_plan(
        conversation=_conversation(), args=_args(), work=_work(), now=_NOW
    )
    repo.create.assert_awaited_once()
    created: Project = repo.create.await_args.args[0]
    assert summary.project == str(created.id)
    assert summary.reused_project is False


async def test_intake_failure_propagates() -> None:
    pipeline = StubWorkPipeline(intake_error=RuntimeError("intake rejected"))
    dispatcher = ConversationalPlanDispatcher(
        project_repo=_project_repo(),
        work_pipeline=pipeline,
        dispatch_port=_RecordingPort(),
    )
    with pytest.raises(RuntimeError, match="intake rejected"):
        await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )


class _LiveProjects:
    """Project store that actually holds what was created.

    The dedupe turns on what the store answers about a project it already has,
    so a repo whose ``get`` always returns ``None`` cannot exercise it.
    """

    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}
        self.created: list[Project] = []

    async def get(self, entity_id: NotBlankStr, /) -> Project | None:
        return self.projects.get(str(entity_id))

    async def create(self, project: Project) -> None:
        if str(project.id) in self.projects:
            msg = f"project {project.id} already exists"
            raise DuplicateRecordError(msg)
        self.projects[str(project.id)] = project
        self.created.append(project)


def _dedupe_dispatcher(
    store: _LiveProjects, window_seconds: float = 300.0
) -> ConversationalPlanDispatcher:
    async def _get_float(namespace: str, key: str) -> float:
        assert (namespace, key) == (
            "chief_of_staff",
            "work_request_dedupe_window_seconds",
        )
        return window_seconds

    return ConversationalPlanDispatcher(
        project_repo=mock_of[ProjectRepository](get=store.get, create=store.create),
        work_pipeline=StubWorkPipeline(),
        dispatch_port=_RecordingPort(),
        config_resolver=mock_of[ConfigResolver](get_float=_get_float),
    )


class TestDuplicateWorkRequest:
    """An impatient re-send joins its request instead of forking a second.

    A buffered turn took fifteen seconds with no feedback, so the operator sent
    the brief again; each send opened its own project, its own plan and its own
    decomposition run over one objective.
    """

    async def test_an_identical_resend_joins_the_request_in_flight(self) -> None:
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(),
            now=_NOW + timedelta(seconds=15),
        )

        assert second.project == first.project
        assert len(store.created) == 1
        # Reported, not silent: an operator told nothing would reasonably
        # believe they had filed two initiatives.
        assert first.reused_project is False
        assert second.reused_project is True

    async def test_wording_that_only_differs_in_spacing_and_case_is_the_same(
        self,
    ) -> None:
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(raw_intent="  Build   the Onboarding flow END TO END. "),
            now=_NOW + timedelta(seconds=5),
        )

        assert second.project == first.project

    async def test_a_reworded_brief_is_a_different_request(self) -> None:
        # Normalisation is spacing and case, never meaning: two briefs that
        # read alike are still two requests, and merging them would drop one.
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(raw_intent="Build the onboarding flow, end to end."),
            now=_NOW + timedelta(seconds=5),
        )

        assert second.project != first.project
        assert len(store.created) == 2

    async def test_a_resend_after_the_window_starts_its_own(self) -> None:
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store, window_seconds=60.0)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(),
            now=_NOW + timedelta(seconds=3600),
        )

        assert second.project != first.project
        assert second.reused_project is False

    async def test_a_zero_window_switches_deduping_off(self) -> None:
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store, window_seconds=0.0)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(),
            now=_NOW + timedelta(seconds=1),
        )

        assert second.project != first.project

    async def test_an_approved_request_is_never_joined(self) -> None:
        # Past PLANNING the plan has been reviewed and dispatched, so folding a
        # new brief in would file work against a decision made about the
        # earlier words.
        store = _LiveProjects()
        dispatcher = _dedupe_dispatcher(store)

        first = await dispatcher.draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        approved = store.projects[str(first.project)]
        store.projects[str(first.project)] = approved.model_copy(
            update={"status": ProjectStatus.ACTIVE}
        )

        second = await dispatcher.draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(),
            now=_NOW + timedelta(seconds=5),
        )

        assert second.project != first.project
        assert second.reused_project is False

    async def test_a_restart_still_dedupes_through_the_derived_id(self) -> None:
        # The in-process record is a cache, not the authority: a second worker
        # (or the same one after a restart) derives the same id from the
        # objective, so the create loses the race and the reuse still happens.
        store = _LiveProjects()
        first = await _dedupe_dispatcher(store).draft_plan(
            conversation=_conversation(), args=_args(), work=_work(), now=_NOW
        )
        second = await _dedupe_dispatcher(store).draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(),
            now=_NOW + timedelta(seconds=5),
        )

        assert second.project == first.project
        assert second.reused_project is True

    async def test_a_named_project_is_never_reported_as_deduped(self) -> None:
        # Filing under a project the operator named is not a duplicate, so
        # telling them their request joined another one would be false.
        store = _LiveProjects()
        store.projects["my-project"] = Project(
            id=as_uuid("existing"),
            name=NotBlankStr("Existing"),
            status=ProjectStatus.PLANNING,
        )
        summary = await _dedupe_dispatcher(store).draft_plan(
            conversation=_conversation(),
            args=_args(),
            work=_work(project="my-project"),
            now=_NOW,
        )

        assert summary.project == "my-project"
        assert summary.reused_project is False

"""Unit tests for :class:`ConversationalPlanDispatcher`.

Covers the seams the proposer suite stubs over: fail-closed dispatch (no port),
project provision-vs-reuse + idempotent re-dispatch, and intake-failure
propagation.
"""

from datetime import UTC, datetime
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


def _work(project: str | None = None) -> ProposedWork:
    return ProposedWork(
        title=NotBlankStr("Ship onboarding"),
        raw_intent=NotBlankStr("Build the onboarding flow end to end."),
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


async def test_absent_project_is_minted_conversation_keyed() -> None:
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
    # The minted project id is deterministic from the conversation (retry-stable).
    assert summary.project == str(created.id)


async def test_duplicate_project_on_redispatch_is_idempotent() -> None:
    repo = _project_repo()
    repo.create.side_effect = DuplicateRecordError("project already provisioned")
    dispatcher = ConversationalPlanDispatcher(
        project_repo=repo,
        work_pipeline=StubWorkPipeline(),
        dispatch_port=_RecordingPort(),
    )
    # The duplicate is swallowed as an idempotent reuse, not raised.
    summary = await dispatcher.draft_plan(
        conversation=_conversation(), args=_args(), work=_work(), now=_NOW
    )
    assert summary.project


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

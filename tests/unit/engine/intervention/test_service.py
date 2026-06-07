"""Tests for the SteeringService write path."""

from collections.abc import Mapping

import pytest

from synthorg.core.enums import InterventionKind, Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.errors import (
    SteeringDirectiveFieldError,
    SteeringKindError,
    SteeringTaskProjectMismatchError,
)
from synthorg.engine.intervention.models import SupersedeMode
from synthorg.engine.intervention.proposer import NoOpSupersessionProposer
from synthorg.engine.intervention.service import SteeringService
from synthorg.observability.events.cockpit import STEERING_DIRECTIVE_ISSUED
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from tests._shared import as_uuid, sid
from tests._shared.steering import FakeBrainService
from tests.unit.api.fakes import FakeProjectBrainRepository

_PROJECT = NotBlankStr("proj-001")


class _FakeTaskEngine:
    def __init__(self, tasks: tuple[Task, ...] = ()) -> None:
        self.cancelled: list[tuple[str, str]] = []
        self._tasks = tasks

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        self.cancelled.append((task_id, reason))
        return (None, None)

    async def get_task(self, task_id: str) -> Task | None:
        return next((t for t in self._tasks if str(t.id) == task_id), None)

    async def list_tasks(
        self, *, status: TaskStatus, project: str, limit: int
    ) -> tuple[tuple[Task, ...], int]:
        items = tuple(
            t for t in self._tasks if t.status == status and t.project == project
        )
        return (items, len(items))


def _task(
    task_id: str,
    status: TaskStatus = TaskStatus.IN_PROGRESS,
    project: NotBlankStr = _PROJECT,
) -> Task:
    return Task(
        id=as_uuid(task_id),
        title=f"Task {task_id}",
        description="A task.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=project,
        created_by="pm",
        assigned_to="agent-1",
        status=status,
    )


def _service(
    *,
    tasks: tuple[Task, ...] = (),
    notifier: object = None,
) -> tuple[SteeringService, FakeProjectBrainRepository, _FakeTaskEngine]:
    repo = FakeProjectBrainRepository()
    task_engine = _FakeTaskEngine(tasks)
    service = SteeringService(
        brain_service=FakeBrainService(repo),  # type: ignore[arg-type]
        brain_repo=repo,
        task_engine=task_engine,  # type: ignore[arg-type]
        proposer=NoOpSupersessionProposer(),
        notifier=notifier,  # type: ignore[arg-type]
    )
    return service, repo, task_engine


@pytest.mark.unit
class TestIssue:
    """Issuing records the directive and handles supersession."""

    async def test_records_directive_and_lists_active(self) -> None:
        service, _repo, _engine = _service()
        result = await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr("use Postgres not Mongo"),
            author=NotBlankStr("mission-control"),
        )
        active = await service.list_active(project_id=_PROJECT)
        assert len(active) == 1
        assert active[0].entry_id == result.directive_id
        assert active[0].kind is InterventionKind.REDIRECT
        assert active[0].text == "use Postgres not Mongo"

    async def test_explicit_supersede_cancels_tasks(self) -> None:
        service, _repo, engine = _service()
        result = await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr("pivot off the frontend"),
            author=NotBlankStr("mission-control"),
            supersede_task_ids=(NotBlankStr("t1"), NotBlankStr("t2")),
            supersede_mode=SupersedeMode.EXPLICIT,
        )
        assert result.superseded_task_ids == ("t1", "t2")
        assert [c[0] for c in engine.cancelled] == ["t1", "t2"]
        assert result.directive_id in engine.cancelled[0][1]

    async def test_propose_returns_proposal_and_cancels_nothing(self) -> None:
        service, _repo, engine = _service(
            tasks=(_task("t1"), _task("t2")),
        )
        result = await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr("pivot off the frontend"),
            author=NotBlankStr("mission-control"),
            supersede_task_ids=(NotBlankStr("t1"),),
            supersede_mode=SupersedeMode.PROPOSE,
        )
        assert result.proposal is not None
        # No-op proposer echoes the seed; nothing cancelled at issue time.
        assert result.proposal.proposed_task_ids == ("t1",)
        assert engine.cancelled == []
        # The directive is still live and confirmable, not consumed by PROPOSE.
        active = await service.list_active(project_id=_PROJECT)
        assert len(active) == 1
        assert active[0].entry_id == result.directive_id

    async def test_brain_entry_written_before_tasks_cancelled(self) -> None:
        # Brain-write-precedes-propagation: the directive must be durably
        # recorded before the first cancellation fires, so a crash
        # mid-supersede can never leave a cancelled task with no directive.
        repo = FakeProjectBrainRepository()
        entries_at_first_cancel: list[int] = []

        class _OrderingEngine:
            async def cancel_task(
                self, task_id: str, *, requested_by: str, reason: str
            ) -> tuple[None, None]:
                rows = await repo.list_current(BrainFilterSpec(project_id=_PROJECT))
                entries_at_first_cancel.append(len(rows))
                return (None, None)

            async def get_task(self, task_id: str) -> Task | None:
                return None

            async def list_tasks(
                self, *, status: TaskStatus, project: str, limit: int
            ) -> tuple[tuple[Task, ...], int]:
                return ((), 0)

        service = SteeringService(
            brain_service=FakeBrainService(repo),  # type: ignore[arg-type]
            brain_repo=repo,
            task_engine=_OrderingEngine(),  # type: ignore[arg-type]
            proposer=NoOpSupersessionProposer(),
        )
        await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr("pivot"),
            author=NotBlankStr("mission-control"),
            supersede_task_ids=(NotBlankStr("t1"),),
            supersede_mode=SupersedeMode.EXPLICIT,
        )
        assert entries_at_first_cancel == [1]

    async def test_task_narrowing_recorded(self) -> None:
        service, _repo, _engine = _service()
        await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.HINT,
            text=NotBlankStr("prefer the existing util"),
            author=NotBlankStr("mission-control"),
            narrow_task_ids=(NotBlankStr("task-9"),),
        )
        active = await service.list_active(project_id=_PROJECT)
        assert active[0].narrow_task_ids == ("task-9",)

    @pytest.mark.parametrize(
        ("project_id", "text", "author"),
        [
            (NotBlankStr(""), NotBlankStr("t"), NotBlankStr("a")),
            (_PROJECT, NotBlankStr("   "), NotBlankStr("a")),
            (_PROJECT, NotBlankStr("t"), NotBlankStr("")),
        ],
    )
    async def test_blank_required_field_rejected(
        self,
        project_id: NotBlankStr,
        text: NotBlankStr,
        author: NotBlankStr,
    ) -> None:
        # NotBlankStr is an identity cast at runtime, so a blank id would
        # otherwise persist a directive with an empty title/summary/author.
        service, _repo, engine = _service()
        with pytest.raises(SteeringDirectiveFieldError):
            await service.issue(
                project_id=project_id,
                kind=InterventionKind.HINT,
                text=text,
                author=author,
            )
        assert engine.cancelled == []

    async def test_non_steerable_kind_rejected(self) -> None:
        # PAUSE/KILL are task-lifecycle interventions, not steering directives;
        # the brain write path rejects them so the inbox never silently drops
        # an entry it cannot propagate.
        service, _repo, _engine = _service()
        with pytest.raises(SteeringKindError):
            await service.issue(
                project_id=_PROJECT,
                kind=InterventionKind.PAUSE,
                text=NotBlankStr("halt"),
                author=NotBlankStr("mission-control"),
            )

    async def test_explicit_supersede_rejects_foreign_project_task(self) -> None:
        # A task from another project must not be cancelled by this project's
        # directive; ownership is validated before any cancellation.
        foreign = _task("foreign", project=NotBlankStr("other-proj"))
        service, _repo, engine = _service(tasks=(foreign,))
        with pytest.raises(SteeringTaskProjectMismatchError):
            await service.issue(
                project_id=_PROJECT,
                kind=InterventionKind.REDIRECT,
                text=NotBlankStr("pivot"),
                author=NotBlankStr("mission-control"),
                supersede_task_ids=(NotBlankStr(sid("foreign")),),
                supersede_mode=SupersedeMode.EXPLICIT,
            )
        assert engine.cancelled == []

    async def test_issue_publishes_ws_event(self) -> None:
        events: list[str] = []

        async def _notifier(event: str, _payload: Mapping[str, object]) -> None:
            events.append(event)

        service, _repo, _engine = _service(notifier=_notifier)
        await service.issue(
            project_id=_PROJECT,
            kind=InterventionKind.REDIRECT,
            text=NotBlankStr("use Postgres"),
            author=NotBlankStr("mission-control"),
        )
        assert STEERING_DIRECTIVE_ISSUED in events


@pytest.mark.unit
class TestConfirmSupersession:
    """Confirming cancels the operator-edited set."""

    async def test_confirm_cancels(self) -> None:
        service, _repo, engine = _service()
        cancelled = await service.confirm_supersession(
            project_id=_PROJECT,
            directive_id=NotBlankStr("d1"),
            task_ids=(NotBlankStr("t1"), NotBlankStr("t2")),
            author=NotBlankStr("mission-control"),
        )
        assert cancelled == ("t1", "t2")
        assert len(engine.cancelled) == 2

    async def test_confirm_rejects_foreign_project_task(self) -> None:
        foreign = _task("foreign", project=NotBlankStr("other-proj"))
        service, _repo, engine = _service(tasks=(foreign,))
        with pytest.raises(SteeringTaskProjectMismatchError):
            await service.confirm_supersession(
                project_id=_PROJECT,
                directive_id=NotBlankStr("d1"),
                task_ids=(NotBlankStr(sid("foreign")),),
                author=NotBlankStr("mission-control"),
            )
        assert engine.cancelled == []

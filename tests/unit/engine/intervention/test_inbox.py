"""Tests for the brain-backed steering inbox projection."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import InterventionKind
from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.inbox import (
    BrainBackedSteeringInbox,
    build_steering_inbox,
)
from synthorg.engine.intervention.models import (
    STEERING_TAG,
    agent_narrow_tag,
    steering_kind_tag,
    task_narrow_tag,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    PlanRevisionPayload,
)
from tests.unit.api.fakes import FakeProjectBrainRepository

_PROJECT = NotBlankStr("proj-001")


def _steering_entry(  # noqa: PLR0913 -- explicit envelope fields for clarity
    *,
    entry_id: str,
    kind: InterventionKind = InterventionKind.REDIRECT,
    text: str = "use Postgres not Mongo",
    status: BrainEntryStatus = BrainEntryStatus.ACTIVE,
    extra_tags: tuple[NotBlankStr, ...] = (),
    revision: int = 1,
) -> BrainEntry:
    return BrainEntry(
        entry_id=NotBlankStr(entry_id),
        revision=revision,
        project_id=_PROJECT,
        entry_kind=BrainEntryKind.PLAN_REVISION,
        title=NotBlankStr("Steering directive"),
        rationale=NotBlankStr(text),
        status=status,
        author=NotBlankStr("mission-control"),
        recorded_at=datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
        tags=(STEERING_TAG, steering_kind_tag(kind), *extra_tags),
        payload=PlanRevisionPayload(summary=NotBlankStr(text)),
    )


async def _seed(repo: FakeProjectBrainRepository, *entries: BrainEntry) -> None:
    for entry in entries:
        await repo.append(entry)


@pytest.mark.unit
class TestSteeringInbox:
    """``pending`` projects the active, applicable, not-yet-adopted directives."""

    async def test_returns_active_redirect(self) -> None:
        repo = FakeProjectBrainRepository()
        await _seed(repo, _steering_entry(entry_id="d1"))
        inbox = build_steering_inbox(repo)
        directives = await inbox.pending(project_id=_PROJECT)
        assert len(directives) == 1
        assert directives[0].entry_id == "d1"
        assert directives[0].kind is InterventionKind.REDIRECT
        assert directives[0].text == "use Postgres not Mongo"

    async def test_excludes_superseded(self) -> None:
        repo = FakeProjectBrainRepository()
        await _seed(
            repo,
            _steering_entry(entry_id="d1", status=BrainEntryStatus.SUPERSEDED),
        )
        inbox = build_steering_inbox(repo)
        assert await inbox.pending(project_id=_PROJECT) == ()

    async def test_excludes_already_adopted(self) -> None:
        repo = FakeProjectBrainRepository()
        await _seed(repo, _steering_entry(entry_id="d1"))
        inbox = build_steering_inbox(repo)
        directives = await inbox.pending(
            project_id=_PROJECT, already_adopted=frozenset({"d1"})
        )
        assert directives == ()

    async def test_excludes_non_steering_plan_revisions(self) -> None:
        repo = FakeProjectBrainRepository()
        plain = BrainEntry(
            entry_id=NotBlankStr("plan-1"),
            revision=1,
            project_id=_PROJECT,
            entry_kind=BrainEntryKind.PLAN_REVISION,
            title=NotBlankStr("Ordinary plan revision"),
            rationale=NotBlankStr("regular plan change"),
            status=BrainEntryStatus.ACTIVE,
            author=NotBlankStr("agent-7"),
            recorded_at=datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
            payload=PlanRevisionPayload(summary=NotBlankStr("regular plan change")),
        )
        await _seed(repo, plain)
        inbox = build_steering_inbox(repo)
        assert await inbox.pending(project_id=_PROJECT) == ()

    async def test_task_narrowing(self) -> None:
        repo = FakeProjectBrainRepository()
        await _seed(
            repo,
            _steering_entry(
                entry_id="d1",
                extra_tags=(task_narrow_tag(NotBlankStr("task-9")),),
            ),
        )
        inbox = build_steering_inbox(repo)
        assert await inbox.pending(project_id=_PROJECT, task_id="task-8") == ()
        hit = await inbox.pending(project_id=_PROJECT, task_id="task-9")
        assert len(hit) == 1

    async def test_agent_narrowing(self) -> None:
        repo = FakeProjectBrainRepository()
        await _seed(
            repo,
            _steering_entry(
                entry_id="d1",
                extra_tags=(agent_narrow_tag(NotBlankStr("agent-9")),),
            ),
        )
        inbox = build_steering_inbox(repo)
        assert await inbox.pending(project_id=_PROJECT, agent_id="agent-8") == ()
        hit = await inbox.pending(project_id=_PROJECT, agent_id="agent-9")
        assert len(hit) == 1

    async def test_read_failure_is_best_effort(self) -> None:
        class _BoomRepo:
            async def list_current(self, *_args: object, **_kwargs: object) -> tuple:
                msg = "db down"
                raise RuntimeError(msg)

        inbox = BrainBackedSteeringInbox(_BoomRepo())  # type: ignore[arg-type]
        assert await inbox.pending(project_id=_PROJECT) == ()

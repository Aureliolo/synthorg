"""Tests for the safe-boundary steering hook (check_steering)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.loop_hook import (
    build_steering_message,
    check_steering,
    resolve_steering_scope,
)
from synthorg.engine.intervention.models import ActiveSteeringDirective
from synthorg.providers.enums import MessageRole

_RECORDED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _directive(
    *,
    entry_id: str = "d1",
    kind: InterventionKind = InterventionKind.REDIRECT,
    text: str = "use Postgres not Mongo",
) -> ActiveSteeringDirective:
    return ActiveSteeringDirective(
        entry_id=NotBlankStr(entry_id),
        kind=kind,
        text=NotBlankStr(text),
        author=NotBlankStr("mission-control"),
        recorded_at=_RECORDED_AT,
    )


class _StubInbox:
    """Returns fixed directives, honouring the consume-once already_adopted set."""

    def __init__(self, directives: tuple[ActiveSteeringDirective, ...]) -> None:
        self._directives = directives
        self.calls: list[frozenset[str]] = []

    async def pending(
        self,
        *,
        project_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
        already_adopted: frozenset[str] = frozenset(),
    ) -> tuple[ActiveSteeringDirective, ...]:
        self.calls.append(frozenset(already_adopted))
        return tuple(d for d in self._directives if d.entry_id not in already_adopted)


class _BoomInbox:
    async def pending(self, **_kwargs: object) -> tuple[ActiveSteeringDirective, ...]:
        msg = "inbox down"
        raise RuntimeError(msg)


@pytest.mark.unit
class TestBuildSteeringMessage:
    """The injected message frames trusted instruction + fenced operator text."""

    def test_redirect_message(self) -> None:
        msg = build_steering_message(_directive(kind=InterventionKind.REDIRECT))
        assert msg.role is MessageRole.USER
        assert msg.content is not None
        assert "REDIRECT" in msg.content
        assert "use Postgres not Mongo" in msg.content
        # SEC-1: operator text is fenced and an untrusted-content
        # directive names the brain-state tag.
        assert "<brain-state>" in msg.content
        assert "</brain-state>" in msg.content
        assert "Any content enclosed in <brain-state>" in msg.content

    def test_hint_message(self) -> None:
        msg = build_steering_message(_directive(kind=InterventionKind.HINT))
        assert msg.content is not None
        assert "HINT" in msg.content
        assert "<brain-state>" in msg.content
        assert "Any content enclosed in <brain-state>" in msg.content


@pytest.mark.unit
class TestResolveScope:
    """Scope resolution returns None outside a project-bound task run."""

    def test_no_task(self, sample_agent_with_personality: AgentIdentity) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        assert resolve_steering_scope(ctx) is None

    def test_resolves_scope(self, sample_agent_context: AgentContext) -> None:
        scope = resolve_steering_scope(sample_agent_context)
        assert scope is not None
        project_id, task_id, _agent_id = scope
        assert sample_agent_context.task_execution is not None
        assert project_id == "proj-001"
        assert task_id == str(sample_agent_context.task_execution.task.id)


@pytest.mark.unit
class TestCheckSteering:
    """check_steering injects, adopts, and flags replan correctly."""

    async def test_none_inbox_returns_none(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await check_steering(sample_agent_context, None, execution_id="e1")
        assert result is None

    async def test_no_scope_returns_none(
        self, sample_agent_with_personality: AgentIdentity
    ) -> None:
        ctx = AgentContext.from_identity(sample_agent_with_personality)
        inbox = _StubInbox((_directive(),))
        assert await check_steering(ctx, inbox, execution_id="e1") is None

    async def test_redirect_injects_and_sets_replan(
        self, sample_agent_context: AgentContext
    ) -> None:
        inbox = _StubInbox((_directive(kind=InterventionKind.REDIRECT),))
        updated = await check_steering(sample_agent_context, inbox, execution_id="e1")
        assert updated is not None
        assert len(updated.conversation) == len(sample_agent_context.conversation) + 1
        assert updated.conversation[-1].role is MessageRole.USER
        assert "d1" in updated.adopted_steering_ids
        assert updated.pending_steering_replan_id == "d1"

    async def test_hint_injects_without_replan(
        self, sample_agent_context: AgentContext
    ) -> None:
        inbox = _StubInbox((_directive(kind=InterventionKind.HINT),))
        updated = await check_steering(sample_agent_context, inbox, execution_id="e1")
        assert updated is not None
        assert "d1" in updated.adopted_steering_ids
        assert updated.pending_steering_replan_id is None

    async def test_multiple_redirects_keep_first_as_replan_trigger(
        self, sample_agent_context: AgentContext
    ) -> None:
        # Two REDIRECTs adopted in one pass: both are injected and adopted,
        # but the replan trigger id is deterministic (the first), not
        # whichever happened to be iterated last.
        inbox = _StubInbox(
            (
                _directive(entry_id="d1", kind=InterventionKind.REDIRECT),
                _directive(entry_id="d2", kind=InterventionKind.REDIRECT),
            )
        )
        updated = await check_steering(sample_agent_context, inbox, execution_id="e1")
        assert updated is not None
        assert updated.adopted_steering_ids >= frozenset({"d1", "d2"})
        assert len(updated.conversation) == len(sample_agent_context.conversation) + 2
        assert updated.pending_steering_replan_id == "d1"

    async def test_nothing_pending_returns_none(
        self, sample_agent_context: AgentContext
    ) -> None:
        inbox = _StubInbox(())
        assert (
            await check_steering(sample_agent_context, inbox, execution_id="e1") is None
        )

    async def test_consume_once_across_calls(
        self, sample_agent_context: AgentContext
    ) -> None:
        inbox = _StubInbox((_directive(),))
        first = await check_steering(sample_agent_context, inbox, execution_id="e1")
        assert first is not None
        second = await check_steering(first, inbox, execution_id="e1")
        assert second is None
        assert inbox.calls[-1] == frozenset({"d1"})

    async def test_inbox_failure_is_best_effort(
        self, sample_agent_context: AgentContext
    ) -> None:
        result = await check_steering(
            sample_agent_context, _BoomInbox(), execution_id="e1"
        )
        assert result is None

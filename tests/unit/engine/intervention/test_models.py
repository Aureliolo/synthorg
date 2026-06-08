"""Tests for the steering directive typed models and tag helpers."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.engine.intervention.enums import InterventionKind
from synthorg.engine.intervention.models import (
    STEERABLE_KINDS,
    STEERING_TAG,
    ActiveSteeringDirective,
    SupersedeMode,
    agent_narrow_tag,
    parse_steering_tags,
    steering_kind_tag,
    task_narrow_tag,
)

_RECORDED_AT = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)


def _directive(
    *,
    kind: InterventionKind = InterventionKind.REDIRECT,
    narrow_task_ids: tuple[NotBlankStr, ...] = (),
    narrow_agent_ids: tuple[NotBlankStr, ...] = (),
) -> ActiveSteeringDirective:
    return ActiveSteeringDirective(
        entry_id=NotBlankStr("dir-1"),
        kind=kind,
        text=NotBlankStr("use Postgres not Mongo"),
        author=NotBlankStr("mission-control"),
        recorded_at=_RECORDED_AT,
        narrow_task_ids=narrow_task_ids,
        narrow_agent_ids=narrow_agent_ids,
    )


@pytest.mark.unit
class TestTagHelpers:
    """Tag formatting and parsing round-trip."""

    def test_kind_tag(self) -> None:
        assert steering_kind_tag(InterventionKind.REDIRECT) == "steering:redirect"
        assert steering_kind_tag(InterventionKind.HINT) == "steering:hint"

    def test_narrow_tags(self) -> None:
        assert task_narrow_tag(NotBlankStr("task-9")) == "steer-task:task-9"
        assert agent_narrow_tag(NotBlankStr("agent-9")) == "steer-agent:agent-9"

    def test_parse_round_trip(self) -> None:
        tags = (
            STEERING_TAG,
            steering_kind_tag(InterventionKind.REDIRECT),
            task_narrow_tag(NotBlankStr("task-9")),
            agent_narrow_tag(NotBlankStr("agent-9")),
        )
        kind, tasks, agents = parse_steering_tags(tags)
        assert kind is InterventionKind.REDIRECT
        assert tasks == ("task-9",)
        assert agents == ("agent-9",)

    def test_parse_without_kind_tag_returns_none(self) -> None:
        kind, tasks, agents = parse_steering_tags((STEERING_TAG,))
        assert kind is None
        assert tasks == ()
        assert agents == ()

    def test_parse_ignores_non_steerable_kind(self) -> None:
        # A pause/kill tag must not be read as a steerable directive kind.
        kind, _, _ = parse_steering_tags((STEERING_TAG, NotBlankStr("steering:pause")))
        assert kind is None


@pytest.mark.unit
class TestActiveSteeringDirective:
    """The live directive view and its invariants."""

    def test_redirect_requires_replan(self) -> None:
        assert _directive(kind=InterventionKind.REDIRECT).requires_replan is True

    def test_hint_does_not_require_replan(self) -> None:
        assert _directive(kind=InterventionKind.HINT).requires_replan is False

    @pytest.mark.parametrize("kind", [InterventionKind.PAUSE, InterventionKind.KILL])
    def test_rejects_non_steerable_kind(self, kind: InterventionKind) -> None:
        with pytest.raises(ValidationError):
            _directive(kind=kind)

    def test_steerable_kinds_membership(self) -> None:
        expected = frozenset({InterventionKind.HINT, InterventionKind.REDIRECT})
        assert expected == STEERABLE_KINDS

    def test_applies_project_wide_without_narrowing(self) -> None:
        directive = _directive()
        assert directive.applies_to(task_id="any", agent_id="any") is True
        assert directive.applies_to(task_id=None, agent_id=None) is True

    def test_task_narrowing_excludes_other_tasks(self) -> None:
        directive = _directive(narrow_task_ids=(NotBlankStr("task-9"),))
        assert directive.applies_to(task_id="task-9", agent_id="a") is True
        assert directive.applies_to(task_id="task-8", agent_id="a") is False
        assert directive.applies_to(task_id=None, agent_id="a") is False

    def test_agent_narrowing_excludes_other_agents(self) -> None:
        directive = _directive(narrow_agent_ids=(NotBlankStr("agent-9"),))
        assert directive.applies_to(task_id="t", agent_id="agent-9") is True
        assert directive.applies_to(task_id="t", agent_id="agent-8") is False


@pytest.mark.unit
class TestSupersedeMode:
    """The supersede mode discriminator."""

    def test_values(self) -> None:
        assert SupersedeMode.NONE.value == "none"
        assert SupersedeMode.EXPLICIT.value == "explicit"
        assert SupersedeMode.PROPOSE.value == "propose"

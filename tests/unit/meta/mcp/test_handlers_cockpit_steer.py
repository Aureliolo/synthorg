"""Unit tests for the cockpit steering MCP handlers.

Covers the success and rejection branches of ``steer`` / ``steer_list`` /
``steer_supersede``: the project-scoped MCP surface mirrors the REST steering
controller and routes through the same ``SteeringService``.
"""

import json

import pytest

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.engine.cockpit.state import CockpitStateSlice
from synthorg.engine.intervention import NoOpSupersessionProposer, SteeringService
from synthorg.meta.mcp.handlers.cockpit import COCKPIT_HANDLERS
from tests._shared import make_app_state
from tests._shared.steering import FakeBrainService
from tests.unit.api.fakes import FakeProjectBrainRepository
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


class _RecordingTaskEngine:
    """Records cancellations so the supersede handler can be asserted."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_task(
        self, task_id: str, *, requested_by: str, reason: str
    ) -> tuple[None, None]:
        self.cancelled.append(task_id)
        return (None, None)

    async def list_tasks(
        self, *, status: object, project: str, limit: int
    ) -> tuple[tuple[object, ...], int]:
        """Mirror the TaskEngine surface the PROPOSE path enumerates."""
        return ((), 0)


@pytest.fixture
def actor() -> AgentIdentity:
    """Caller-authenticated actor passed to the handler."""
    return make_test_actor(name="steer-caller")


def _state_with_steering(engine: _RecordingTaskEngine | None = None) -> AppState:
    repo = FakeProjectBrainRepository()
    steering = SteeringService(
        brain_service=FakeBrainService(repo),  # type: ignore[arg-type]
        brain_repo=repo,
        task_engine=engine or _RecordingTaskEngine(),  # type: ignore[arg-type]
        proposer=NoOpSupersessionProposer(),
    )
    return make_app_state(
        slices={CockpitStateSlice: {"steering_service": steering}},
    )


class TestSteerHandlers:
    async def test_steer_issues_directive(self, actor: AgentIdentity) -> None:
        state = _state_with_steering()
        handler = COCKPIT_HANDLERS["synthorg_cockpit_steer"]
        raw = await handler(
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "kind": "redirect",
                "text": "use Postgres not Mongo",
                "reason": "operator redirect",
                "confirm": True,
            },
            actor=actor,
        )
        body = json.loads(raw)
        assert body["status"] == "ok"
        assert body["data"]["kind"] == "redirect"
        assert body["data"]["directive_id"]

    async def test_steer_rejects_pause_kind(self, actor: AgentIdentity) -> None:
        state = _state_with_steering()
        handler = COCKPIT_HANDLERS["synthorg_cockpit_steer"]
        raw = await handler(
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "kind": "pause",
                "text": "halt",
                "reason": "x",
                "confirm": True,
            },
            actor=actor,
        )
        body = json.loads(raw)
        assert body["status"] == "error"
        assert body["error_type"] == "SteeringKindError"

    async def test_steer_list_returns_issued_directive(
        self, actor: AgentIdentity
    ) -> None:
        state = _state_with_steering()
        await COCKPIT_HANDLERS["synthorg_cockpit_steer"](
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "kind": "hint",
                "text": "prefer the existing util",
                "reason": "operator hint",
                "confirm": True,
            },
            actor=actor,
        )
        raw = await COCKPIT_HANDLERS["synthorg_cockpit_steer_list"](
            app_state=state,
            arguments={"project_id": "proj-1"},
            actor=actor,
        )
        body = json.loads(raw)
        assert body["status"] == "ok"
        assert len(body["data"]) == 1
        assert body["data"][0]["text"] == "prefer the existing util"

    async def test_steer_supersede_cancels_tasks(self, actor: AgentIdentity) -> None:
        engine = _RecordingTaskEngine()
        state = _state_with_steering(engine)
        issued = await COCKPIT_HANDLERS["synthorg_cockpit_steer"](
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "kind": "redirect",
                "text": "pivot",
                "reason": "operator redirect",
                "confirm": True,
            },
            actor=actor,
        )
        directive_id = json.loads(issued)["data"]["directive_id"]
        raw = await COCKPIT_HANDLERS["synthorg_cockpit_steer_supersede"](
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "directive_id": directive_id,
                "task_ids": ["t1", "t2"],
                "reason": "operator supersede",
                "confirm": True,
            },
            actor=actor,
        )
        body = json.loads(raw)
        assert body["status"] == "ok"
        assert body["data"]["cancelled_task_ids"] == ["t1", "t2"]
        assert engine.cancelled == ["t1", "t2"]

    async def test_steer_supersede_rejects_empty_task_ids(
        self, actor: AgentIdentity
    ) -> None:
        # An absent/empty task_ids set must error, not silently confirm a
        # zero-task supersession the operator never sees.
        engine = _RecordingTaskEngine()
        state = _state_with_steering(engine)
        raw = await COCKPIT_HANDLERS["synthorg_cockpit_steer_supersede"](
            app_state=state,
            arguments={
                "project_id": "proj-1",
                "directive_id": "directive-1",
                "reason": "operator supersede",
                "confirm": True,
            },
            actor=actor,
        )
        body = json.loads(raw)
        assert body["status"] == "error"
        assert engine.cancelled == []

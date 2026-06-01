"""Tests for the cockpit-channel steering WS notifier.

Asserts the notifier actually builds and publishes a valid ``WsEvent`` (so the
payload models match the dicts ``SteeringService._notify`` emits), and that an
event without a matching ``WsEventType`` (a worker-only steering event) is
swallowed rather than aborting the steering write path.
"""

import json

import pytest

from synthorg.api.app_helpers import make_steering_notifier
from synthorg.api.ws_models import WsEventType
from synthorg.observability.events.cockpit import (
    STEERING_DIRECTIVE_ADOPTED,
    STEERING_DIRECTIVE_ISSUED,
    STEERING_SUPERSESSION_PROPOSED,
    STEERING_TASKS_SUPERSEDED,
)

pytestmark = pytest.mark.unit


class _SpyChannels:
    """Records ``publish`` calls without a real channels backend."""

    def __init__(self) -> None:
        self.published: list[tuple[str, list[str]]] = []

    def publish(self, data: str | bytes, channels: list[str]) -> None:
        self.published.append((str(data), channels))


class TestSteeringNotifier:
    async def test_issued_event_publishes_valid_envelope(self) -> None:
        spy = _SpyChannels()
        notify = make_steering_notifier(spy)  # type: ignore[arg-type]
        await notify(
            STEERING_DIRECTIVE_ISSUED,
            {"project_id": "p1", "directive_id": "d1", "kind": "redirect"},
        )
        assert len(spy.published) == 1
        data, channels = spy.published[0]
        assert channels == ["cockpit"]
        envelope = json.loads(data)
        assert envelope["event_type"] == WsEventType.STEERING_DIRECTIVE_ISSUED.value
        assert envelope["payload"]["directive_id"] == "d1"
        assert envelope["payload"]["kind"] == "redirect"

    async def test_proposed_event_carries_task_ids(self) -> None:
        spy = _SpyChannels()
        notify = make_steering_notifier(spy)  # type: ignore[arg-type]
        await notify(
            STEERING_SUPERSESSION_PROPOSED,
            {"project_id": "p1", "directive_id": "d1", "proposed_task_ids": ["t1"]},
        )
        envelope = json.loads(spy.published[0][0])
        assert envelope["payload"]["proposed_task_ids"] == ["t1"]

    async def test_superseded_event_carries_task_ids(self) -> None:
        spy = _SpyChannels()
        notify = make_steering_notifier(spy)  # type: ignore[arg-type]
        await notify(
            STEERING_TASKS_SUPERSEDED,
            {"project_id": "p1", "directive_id": "d1", "task_ids": ["t1", "t2"]},
        )
        envelope = json.loads(spy.published[0][0])
        assert envelope["payload"]["task_ids"] == ["t1", "t2"]

    async def test_unmapped_worker_event_is_swallowed(self) -> None:
        spy = _SpyChannels()
        notify = make_steering_notifier(spy)  # type: ignore[arg-type]
        # ADOPTED is a worker-side observability event with no WsEventType;
        # the enum lookup raises ValueError which the notifier swallows.
        await notify(STEERING_DIRECTIVE_ADOPTED, {"project_id": "p1"})
        assert spy.published == []

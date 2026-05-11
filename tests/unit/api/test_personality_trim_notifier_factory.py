"""Unit tests for ``synthorg.api.app.make_personality_trim_notifier``.

Covers the factory that wires the personality-trim WebSocket event onto a
Litestar ``ChannelsPlugin``.  The factory is the only glue between the
engine's notifier contract and the live dashboard, so regressions here
silently break every personality-trim toast in production.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from litestar.channels.plugin import ChannelsPlugin

from synthorg.api.app_helpers import make_personality_trim_notifier
from synthorg.api.channels import CHANNEL_AGENTS
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.engine.agent_engine import PersonalityTrimPayload


def _make_payload() -> PersonalityTrimPayload:
    """Build a representative ``PersonalityTrimPayload`` for factory tests."""
    return {
        "agent_id": "agent-1",
        "agent_name": "Test Agent",
        "task_id": "task-42",
        "before_tokens": 600,
        "after_tokens": 200,
        "max_tokens": 300,
        "trim_tier": 2,
        "budget_met": True,
    }


@pytest.mark.unit
class TestMakePersonalityTrimNotifier:
    """Tests for the ``make_personality_trim_notifier`` factory."""

    async def test_publish_success(self) -> None:
        """Happy path: factory publishes a valid ``WsEvent`` on the agents channel."""
        channels = MagicMock(spec=ChannelsPlugin)
        channels.publish = MagicMock()
        notifier = make_personality_trim_notifier(channels)
        payload = _make_payload()

        await notifier(payload)

        assert channels.publish.call_count == 1
        call_args = channels.publish.call_args
        published_json = call_args.args[0]
        assert call_args.kwargs["channels"] == [CHANNEL_AGENTS]

        decoded = json.loads(published_json)
        assert decoded["event_type"] == WsEventType.PERSONALITY_TRIMMED.value
        assert decoded["event_type"] == "personality.trimmed"
        assert decoded["channel"] == CHANNEL_AGENTS
        assert decoded["payload"] == dict(payload)

        # Round-trip validation: the published JSON must deserialize back
        # into a valid WsEvent.  Catches silent breakage if the payload dict
        # ever contains a non-JSON-serializable value.
        WsEvent.model_validate_json(published_json)

    async def test_publish_error_is_swallowed(self) -> None:
        """Publish failures are logged and swallowed (best-effort contract)."""
        channels = MagicMock(spec=ChannelsPlugin)
        channels.publish = MagicMock(side_effect=RuntimeError("broker down"))
        notifier = make_personality_trim_notifier(channels)

        # Must not raise.
        await notifier(_make_payload())

        assert channels.publish.call_count == 1

    async def test_callable_is_reusable_across_invocations(self) -> None:
        """The factory returns a reusable closure -- multiple invocations work."""
        channels = MagicMock(spec=ChannelsPlugin)
        channels.publish = MagicMock()
        notifier = make_personality_trim_notifier(channels)

        await notifier(_make_payload())
        await notifier(_make_payload())
        await notifier(_make_payload())

        assert channels.publish.call_count == 3

    async def test_errors_on_one_invocation_do_not_poison_next(self) -> None:
        """A failing publish must not break subsequent invocations."""
        channels = MagicMock(spec=ChannelsPlugin)
        channels.publish = MagicMock(
            side_effect=[RuntimeError("transient"), None, None],
        )
        notifier = make_personality_trim_notifier(channels)

        # First call: publish raises, notifier swallows.
        await notifier(_make_payload())
        # Second and third calls: succeed.
        await notifier(_make_payload())
        await notifier(_make_payload())

        assert channels.publish.call_count == 3

    @pytest.mark.parametrize(
        "exc_type",
        [asyncio.CancelledError, MemoryError, RecursionError],
    )
    async def test_base_exceptions_propagate(
        self,
        exc_type: type[BaseException],
    ) -> None:
        """``BaseException`` subclasses propagate out of the publisher.

        ``MemoryError`` and ``RecursionError`` are explicitly re-raised by
        the ``except MemoryError, RecursionError:`` guard.
        ``asyncio.CancelledError`` propagates naturally because it is a
        ``BaseException`` subclass and is not caught by ``except Exception``.
        """
        channels = MagicMock(spec=ChannelsPlugin)
        channels.publish = MagicMock(side_effect=exc_type())
        notifier = make_personality_trim_notifier(channels)

        with pytest.raises(exc_type):
            await notifier(_make_payload())

"""Telemetry correctness for the NATS durable-consumer unsubscribe path.

A failed consumer teardown must log a single WARNING and stop: it must
not also emit the success ``COMM_SUBSCRIPTION_REMOVED`` INFO for the same
operation, which would mislead telemetry consumers into seeing both a
"failed" and a "removed" event for one unsubscribe.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from synthorg.communication.bus import _nats_consumers
from synthorg.communication.bus._nats_consumers import unsubscribe
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType
from synthorg.observability.events.communication import COMM_SUBSCRIPTION_REMOVED

pytestmark = pytest.mark.unit


class _RecordingLogger:
    """Records structured-log events without a Mock (mock-spec gate)."""

    def __init__(self) -> None:
        self.info_events: list[str] = []
        self.warning_events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.info_events.append(event)

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warning_events.append((event, kwargs))

    def debug(self, event: str, **kwargs: Any) -> None:
        pass

    def error(self, event: str, **kwargs: Any) -> None:
        pass


def _state_with_sub(sub: object) -> SimpleNamespace:
    """A minimal ``_NatsState`` stand-in carrying one subscription.

    ``kv=None`` makes ``write_channel_to_kv`` a no-op so the test does
    not need a working KV bucket.
    """
    channel = Channel(name="#chan", type=ChannelType.TOPIC, subscribers=("sub-1",))
    return SimpleNamespace(
        lock=asyncio.Lock(),
        running=True,
        channels={"#chan": channel},
        subscriptions={("#chan", "sub-1"): sub},
        last_overflow_log={},
        kv=None,
    )


async def test_unsubscribe_failure_skips_success_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _RecordingLogger()
    monkeypatch.setattr(_nats_consumers, "logger", recording)

    async def _boom_unsubscribe() -> None:
        msg = "consumer teardown boom"
        raise RuntimeError(msg)

    state = _state_with_sub(SimpleNamespace(unsubscribe=_boom_unsubscribe))

    await unsubscribe(state, "#chan", "sub-1")  # type: ignore[arg-type]

    # The teardown failure logs exactly one WARNING and returns; the
    # success COMM_SUBSCRIPTION_REMOVED INFO must NOT also fire.
    assert recording.info_events == []
    assert len(recording.warning_events) == 1
    event, kwargs = recording.warning_events[0]
    assert event == COMM_SUBSCRIPTION_REMOVED
    assert kwargs["phase"] == "unsubscribe_consumer_failed"
    # Pin the redacted error fields so a regression that drops them is caught.
    assert kwargs["error_type"] == "RuntimeError"
    assert isinstance(kwargs["error"], str)
    assert kwargs["error"]


async def test_unsubscribe_success_emits_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _RecordingLogger()
    monkeypatch.setattr(_nats_consumers, "logger", recording)

    async def _ok_unsubscribe() -> None:
        return None

    state = _state_with_sub(SimpleNamespace(unsubscribe=_ok_unsubscribe))

    await unsubscribe(state, "#chan", "sub-1")  # type: ignore[arg-type]

    # A clean teardown emits the success INFO and no WARNING.
    assert recording.info_events == [COMM_SUBSCRIPTION_REMOVED]
    assert recording.warning_events == []

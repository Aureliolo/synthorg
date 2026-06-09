"""Error-path coverage for the NATS KV channel helpers.

Transport failures while reading / writing the KV bucket must wrap into
``BusStreamError`` (read/create) or be swallowed (best-effort write),
and never absorb an interpreter-critical exception.
"""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from typeguard import suppress_type_checks

from synthorg.communication.bus._nats_kv import (
    create_channel_in_kv,
    fetch_kv_entry,
    scan_kv_channels,
    write_channel_to_kv,
)
from synthorg.communication.bus.errors import BusStreamError
from synthorg.communication.channel import Channel
from synthorg.communication.enums import ChannelType

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _suppress_typeguard_for_nats_state_doubles() -> Iterator[None]:
    """Suppress typeguard module-wide for the NATS KV error-path tests.

    Each test passes a ``SimpleNamespace(kv=...)`` stand-in for the concrete
    ``_NatsState`` whose ``kv`` operations are scripted to raise; the tests
    verify the error-wrapping behaviour, not ``_NatsState`` type conformance.
    A concrete-class ``isinstance`` check cannot be satisfied by a structural
    double without reconstructing a live NATS connection, so the runtime check
    is suppressed for the module.
    """
    with suppress_type_checks():
        yield


async def _boom(*_args: object, **_kwargs: object) -> object:
    """Async KV operation that always fails with a non-critical error."""
    msg = "kv transport boom"
    raise RuntimeError(msg)


def _failing_kv() -> SimpleNamespace:
    """A KV stand-in whose every operation raises a non-critical error."""
    return SimpleNamespace(create=_boom, put=_boom, get=_boom, keys=_boom)


def _channel() -> Channel:
    return Channel(name="#kv-error", type=ChannelType.TOPIC)


async def test_create_channel_kv_failure_wraps_bus_stream_error() -> None:
    state = SimpleNamespace(kv=_failing_kv())
    with pytest.raises(BusStreamError):
        await create_channel_in_kv(state, _channel())  # type: ignore[arg-type]


async def test_write_channel_kv_failure_is_swallowed() -> None:
    state = SimpleNamespace(kv=_failing_kv())
    # Best-effort persistence: a put failure is logged, not raised.
    await write_channel_to_kv(state, _channel())  # type: ignore[arg-type]


async def test_fetch_kv_entry_failure_wraps_bus_stream_error() -> None:
    state = SimpleNamespace(kv=_failing_kv())
    with pytest.raises(BusStreamError):
        await fetch_kv_entry(state, "#kv-error")  # type: ignore[arg-type]


async def test_scan_kv_keys_failure_wraps_bus_stream_error() -> None:
    state = SimpleNamespace(kv=_failing_kv())
    with pytest.raises(BusStreamError):
        await scan_kv_channels(state)  # type: ignore[arg-type]


async def test_scan_kv_skips_undecodable_keys() -> None:
    async def _keys_with_bad_token(*_args: object, **_kwargs: object) -> list[str]:
        return ["####not-a-valid-token####"]

    kv = _failing_kv()
    # keys() succeeds but returns an undecodable token; the per-key
    # decode failure is logged and skipped, and the get() failure for
    # any decoded key is captured by the gather, so the scan returns [].
    kv.keys = _keys_with_bad_token
    state = SimpleNamespace(kv=kv)

    result = await scan_kv_channels(state)  # type: ignore[arg-type]

    assert result == []

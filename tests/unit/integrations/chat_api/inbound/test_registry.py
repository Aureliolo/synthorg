"""Tests for the inbound thread -> approval correlation registry."""

import pytest

from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry

pytestmark = pytest.mark.unit


class TestInboundThreadRegistry:
    def test_register_then_resolve(self) -> None:
        reg = InboundThreadRegistry()
        reg.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        assert reg.resolve(channel="C1", thread_ts="100.1") == "ap-1"

    def test_resolve_unknown_is_none(self) -> None:
        reg = InboundThreadRegistry()
        assert reg.resolve(channel="C1", thread_ts="nope") is None

    def test_channel_scoped(self) -> None:
        reg = InboundThreadRegistry()
        reg.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        assert reg.resolve(channel="C2", thread_ts="100.1") is None

    def test_blank_inputs_are_ignored(self) -> None:
        reg = InboundThreadRegistry()
        reg.register(channel="", thread_ts="100.1", approval_id="ap-1")
        reg.register(channel="C1", thread_ts="", approval_id="ap-1")
        reg.register(channel="C1", thread_ts="100.1", approval_id="")
        assert reg.resolve(channel="C1", thread_ts="100.1") is None

    def test_discard(self) -> None:
        reg = InboundThreadRegistry()
        reg.register(channel="C1", thread_ts="100.1", approval_id="ap-1")
        reg.discard(channel="C1", thread_ts="100.1")
        assert reg.resolve(channel="C1", thread_ts="100.1") is None

    def test_eviction_past_capacity(self) -> None:
        reg = InboundThreadRegistry(capacity=2)
        reg.register(channel="C", thread_ts="1", approval_id="a1")
        reg.register(channel="C", thread_ts="2", approval_id="a2")
        reg.register(channel="C", thread_ts="3", approval_id="a3")
        # Oldest evicted.
        assert reg.resolve(channel="C", thread_ts="1") is None
        assert reg.resolve(channel="C", thread_ts="2") == "a2"
        assert reg.resolve(channel="C", thread_ts="3") == "a3"

    def test_zero_capacity_rejected(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            InboundThreadRegistry(capacity=0)

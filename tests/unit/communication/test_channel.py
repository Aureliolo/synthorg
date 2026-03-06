"""Tests for the Channel domain model."""

import pytest
from pydantic import ValidationError

from ai_company.communication.channel import Channel
from ai_company.communication.enums import ChannelType

pytestmark = pytest.mark.timeout(30)


# ── Channel: Construction & Defaults ────────────────────────────


@pytest.mark.unit
class TestChannelConstruction:
    def test_minimal_valid(self) -> None:
        ch = Channel(name="#engineering")
        assert ch.name == "#engineering"
        assert ch.type is ChannelType.TOPIC
        assert ch.subscribers == ()

    def test_all_fields_set(self) -> None:
        ch = Channel(
            name="#backend",
            type=ChannelType.DIRECT,
            subscribers=("agent-a", "agent-b"),
        )
        assert ch.name == "#backend"
        assert ch.type is ChannelType.DIRECT
        assert ch.subscribers == ("agent-a", "agent-b")


# ── Channel: Validation ─────────────────────────────────────────


@pytest.mark.unit
class TestChannelValidation:
    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Channel(name="")

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Channel(name="   ")

    def test_empty_subscriber_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            Channel(name="#test", subscribers=("agent-a", ""))

    def test_whitespace_subscriber_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Channel(name="#test", subscribers=("agent-a", "  "))

    def test_duplicate_subscribers_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in subscribers"):
            Channel(name="#test", subscribers=("agent-a", "agent-b", "agent-a"))

    def test_valid_subscribers(self) -> None:
        ch = Channel(name="#test", subscribers=("a", "b", "c"))
        assert ch.subscribers == ("a", "b", "c")


# ── Channel: Immutability ───────────────────────────────────────


@pytest.mark.unit
class TestChannelImmutability:
    def test_frozen(self) -> None:
        ch = Channel(name="#test")
        with pytest.raises(ValidationError):
            ch.name = "#other"  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = Channel(name="#test", subscribers=("a",))
        updated = original.model_copy(update={"name": "#updated"})
        assert updated.name == "#updated"
        assert original.name == "#test"


# ── Channel: Serialization ──────────────────────────────────────


@pytest.mark.unit
class TestChannelSerialization:
    def test_json_roundtrip(self) -> None:
        ch = Channel(
            name="#engineering",
            type=ChannelType.BROADCAST,
            subscribers=("a", "b"),
        )
        restored = Channel.model_validate_json(ch.model_dump_json())
        assert restored == ch

    def test_model_dump(self) -> None:
        ch = Channel(name="#test", type=ChannelType.DIRECT)
        dumped = ch.model_dump()
        assert dumped["type"] == "direct"


# ── Channel: Factory ────────────────────────────────────────────


@pytest.mark.unit
class TestChannelFactory:
    def test_factory(self) -> None:
        from tests.unit.communication.conftest import ChannelFactory

        ch = ChannelFactory.build()
        assert isinstance(ch, Channel)
        assert len(ch.name) >= 1


# ── Channel: Fixtures ───────────────────────────────────────────


@pytest.mark.unit
class TestChannelFixtures:
    def test_sample_channel(self, sample_channel: Channel) -> None:
        assert sample_channel.name == "#engineering"
        assert sample_channel.type is ChannelType.TOPIC
        assert len(sample_channel.subscribers) == 2

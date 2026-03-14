"""Tests for the communication configuration models."""

import pytest
from pydantic import ValidationError

from synthorg.communication.config import (
    _DEFAULT_CHANNELS,
    CircuitBreakerConfig,
    CommunicationConfig,
    HierarchyConfig,
    LoopPreventionConfig,
    MeetingsConfig,
    MeetingTypeConfig,
    MessageBusConfig,
    RateLimitConfig,
)
from synthorg.communication.enums import (
    CommunicationPattern,
    MessageBusBackend,
)
from synthorg.communication.meeting.frequency import MeetingFrequency

pytestmark = pytest.mark.timeout(30)


# ── MessageBusConfig ────────────────────────────────────────────


@pytest.mark.unit
class TestMessageBusConfigDefaults:
    def test_defaults(self) -> None:
        cfg = MessageBusConfig()
        assert cfg.backend is MessageBusBackend.INTERNAL
        assert cfg.channels == _DEFAULT_CHANNELS

    def test_custom_values(self) -> None:
        cfg = MessageBusConfig(
            backend=MessageBusBackend.REDIS,
            channels=("#ops", "#alerts"),
        )
        assert cfg.backend is MessageBusBackend.REDIS
        assert cfg.channels == ("#ops", "#alerts")


@pytest.mark.unit
class TestMessageBusConfigValidation:
    def test_empty_channel_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 1 character"):
            MessageBusConfig(channels=("#valid", ""))

    def test_whitespace_channel_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MessageBusConfig(channels=("#valid", "  "))

    def test_duplicate_channels_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in channels"):
            MessageBusConfig(channels=("#a", "#b", "#a"))

    def test_empty_channels_allowed(self) -> None:
        cfg = MessageBusConfig(channels=())
        assert cfg.channels == ()


@pytest.mark.unit
class TestMessageBusConfigImmutability:
    def test_frozen(self) -> None:
        cfg = MessageBusConfig()
        with pytest.raises(ValidationError):
            cfg.backend = MessageBusBackend.KAFKA  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = MessageBusConfig()
        updated = original.model_copy(update={"backend": MessageBusBackend.RABBITMQ})
        assert updated.backend is MessageBusBackend.RABBITMQ
        assert original.backend is MessageBusBackend.INTERNAL


@pytest.mark.unit
class TestMessageBusConfigSerialization:
    def test_json_roundtrip(self) -> None:
        cfg = MessageBusConfig(
            backend=MessageBusBackend.KAFKA,
            channels=("#a", "#b"),
        )
        restored = MessageBusConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import MessageBusConfigFactory

        cfg = MessageBusConfigFactory.build()
        assert isinstance(cfg, MessageBusConfig)


# ── MeetingTypeConfig ───────────────────────────────────────────


@pytest.mark.unit
class TestMeetingTypeConfigConstruction:
    def test_with_frequency(self) -> None:
        mt = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        assert mt.name == "standup"
        assert mt.frequency == "daily"
        assert mt.trigger is None
        assert mt.participants == ()
        assert mt.duration_tokens == 2000

    def test_with_trigger(self) -> None:
        mt = MeetingTypeConfig(name="code_review", trigger="on_pr")
        assert mt.trigger == "on_pr"
        assert mt.frequency is None

    def test_custom_values(self) -> None:
        mt = MeetingTypeConfig(
            name="planning",
            frequency=MeetingFrequency.BI_WEEKLY,
            participants=("all",),
            duration_tokens=5000,
        )
        assert mt.participants == ("all",)
        assert mt.duration_tokens == 5000


@pytest.mark.unit
class TestMeetingTypeConfigValidation:
    def test_both_frequency_and_trigger_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Only one of frequency or trigger may be set",
        ):
            MeetingTypeConfig(
                name="bad", frequency=MeetingFrequency.DAILY, trigger="on_pr"
            )

    def test_neither_frequency_nor_trigger_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Exactly one of frequency or trigger must be set",
        ):
            MeetingTypeConfig(name="bad")

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MeetingTypeConfig(name="", frequency=MeetingFrequency.DAILY)

    def test_whitespace_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MeetingTypeConfig(name="   ", frequency=MeetingFrequency.DAILY)

    def test_whitespace_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Input should be"):
            MeetingTypeConfig(name="standup", frequency="   ")  # type: ignore[arg-type]

    def test_whitespace_trigger_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MeetingTypeConfig(name="review", trigger="   ")

    def test_whitespace_participant_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="whitespace-only",
        ):
            MeetingTypeConfig(
                name="standup",
                frequency=MeetingFrequency.DAILY,
                participants=("eng", "  "),
            )

    def test_empty_participant_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="at least 1 character",
        ):
            MeetingTypeConfig(
                name="standup",
                frequency=MeetingFrequency.DAILY,
                participants=("eng", ""),
            )

    def test_duplicate_participants_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate entries in participants"):
            MeetingTypeConfig(
                name="standup",
                frequency=MeetingFrequency.DAILY,
                participants=("eng", "qa", "eng"),
            )

    def test_zero_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MeetingTypeConfig(
                name="bad", frequency=MeetingFrequency.DAILY, duration_tokens=0
            )

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MeetingTypeConfig(
                name="bad", frequency=MeetingFrequency.DAILY, duration_tokens=-1
            )


@pytest.mark.unit
class TestMeetingTypeConfigImmutability:
    def test_frozen(self) -> None:
        mt = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        with pytest.raises(ValidationError):
            mt.name = "new"  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        updated = original.model_copy(update={"duration_tokens": 3000})
        assert updated.duration_tokens == 3000
        assert original.duration_tokens == 2000


@pytest.mark.unit
class TestMeetingTypeConfigSerialization:
    def test_json_roundtrip(self) -> None:
        mt = MeetingTypeConfig(
            name="standup",
            frequency=MeetingFrequency.DAILY,
            participants=("eng",),
            duration_tokens=1500,
        )
        restored = MeetingTypeConfig.model_validate_json(mt.model_dump_json())
        assert restored == mt

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import MeetingTypeConfigFactory

        mt = MeetingTypeConfigFactory.build()
        assert isinstance(mt, MeetingTypeConfig)


# ── MeetingsConfig ──────────────────────────────────────────────


@pytest.mark.unit
class TestMeetingsConfigConstruction:
    def test_defaults(self) -> None:
        cfg = MeetingsConfig()
        assert cfg.enabled is True
        assert cfg.types == ()

    def test_custom_values(self) -> None:
        mt = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        cfg = MeetingsConfig(enabled=False, types=(mt,))
        assert cfg.enabled is False
        assert len(cfg.types) == 1


@pytest.mark.unit
class TestMeetingsConfigValidation:
    def test_duplicate_meeting_names_rejected(self) -> None:
        mt1 = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        mt2 = MeetingTypeConfig(name="standup", trigger="on_pr")
        with pytest.raises(ValidationError, match="Duplicate meeting type names"):
            MeetingsConfig(types=(mt1, mt2))

    def test_unique_meeting_names_accepted(self) -> None:
        mt1 = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        mt2 = MeetingTypeConfig(name="review", trigger="on_pr")
        cfg = MeetingsConfig(types=(mt1, mt2))
        assert len(cfg.types) == 2


@pytest.mark.unit
class TestMeetingsConfigImmutability:
    def test_frozen(self) -> None:
        cfg = MeetingsConfig()
        with pytest.raises(ValidationError):
            cfg.enabled = False  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        mt = MeetingTypeConfig(name="standup", frequency=MeetingFrequency.DAILY)
        cfg = MeetingsConfig(types=(mt,))
        restored = MeetingsConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import MeetingsConfigFactory

        cfg = MeetingsConfigFactory.build()
        assert isinstance(cfg, MeetingsConfig)


# ── HierarchyConfig ────────────────────────────────────────────


@pytest.mark.unit
class TestHierarchyConfig:
    def test_defaults(self) -> None:
        cfg = HierarchyConfig()
        assert cfg.enforce_chain_of_command is True
        assert cfg.allow_skip_level is False

    def test_custom_values(self) -> None:
        cfg = HierarchyConfig(enforce_chain_of_command=False, allow_skip_level=True)
        assert cfg.enforce_chain_of_command is False
        assert cfg.allow_skip_level is True

    def test_frozen(self) -> None:
        cfg = HierarchyConfig()
        with pytest.raises(ValidationError):
            cfg.allow_skip_level = True  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = HierarchyConfig(enforce_chain_of_command=False, allow_skip_level=True)
        restored = HierarchyConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_model_copy(self) -> None:
        original = HierarchyConfig()
        updated = original.model_copy(update={"allow_skip_level": True})
        assert updated.allow_skip_level is True
        assert original.allow_skip_level is False

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import HierarchyConfigFactory

        cfg = HierarchyConfigFactory.build()
        assert isinstance(cfg, HierarchyConfig)


# ── RateLimitConfig ─────────────────────────────────────────────


@pytest.mark.unit
class TestRateLimitConfig:
    def test_defaults(self) -> None:
        cfg = RateLimitConfig()
        assert cfg.max_per_pair_per_minute == 10
        assert cfg.burst_allowance == 3

    def test_custom_values(self) -> None:
        cfg = RateLimitConfig(max_per_pair_per_minute=20, burst_allowance=5)
        assert cfg.max_per_pair_per_minute == 20
        assert cfg.burst_allowance == 5

    def test_zero_rate_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RateLimitConfig(max_per_pair_per_minute=0)

    def test_negative_rate_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RateLimitConfig(max_per_pair_per_minute=-1)

    def test_zero_burst_allowed(self) -> None:
        cfg = RateLimitConfig(burst_allowance=0)
        assert cfg.burst_allowance == 0

    def test_negative_burst_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RateLimitConfig(burst_allowance=-1)

    def test_frozen(self) -> None:
        cfg = RateLimitConfig()
        with pytest.raises(ValidationError):
            cfg.max_per_pair_per_minute = 20  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = RateLimitConfig(max_per_pair_per_minute=15, burst_allowance=2)
        restored = RateLimitConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import RateLimitConfigFactory

        cfg = RateLimitConfigFactory.build()
        assert isinstance(cfg, RateLimitConfig)


# ── CircuitBreakerConfig ────────────────────────────────────────


@pytest.mark.unit
class TestCircuitBreakerConfig:
    def test_defaults(self) -> None:
        cfg = CircuitBreakerConfig()
        assert cfg.bounce_threshold == 3
        assert cfg.cooldown_seconds == 300

    def test_custom_values(self) -> None:
        cfg = CircuitBreakerConfig(bounce_threshold=5, cooldown_seconds=600)
        assert cfg.bounce_threshold == 5
        assert cfg.cooldown_seconds == 600

    def test_zero_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CircuitBreakerConfig(bounce_threshold=0)

    def test_zero_cooldown_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CircuitBreakerConfig(cooldown_seconds=0)

    def test_frozen(self) -> None:
        cfg = CircuitBreakerConfig()
        with pytest.raises(ValidationError):
            cfg.bounce_threshold = 5  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        cfg = CircuitBreakerConfig(bounce_threshold=10, cooldown_seconds=120)
        restored = CircuitBreakerConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import CircuitBreakerConfigFactory

        cfg = CircuitBreakerConfigFactory.build()
        assert isinstance(cfg, CircuitBreakerConfig)


# ── LoopPreventionConfig ───────────────────────────────────────


@pytest.mark.unit
class TestLoopPreventionConfigDefaults:
    def test_defaults(self) -> None:
        cfg = LoopPreventionConfig()
        assert cfg.max_delegation_depth == 5
        assert isinstance(cfg.rate_limit, RateLimitConfig)
        assert cfg.dedup_window_seconds == 60
        assert isinstance(cfg.circuit_breaker, CircuitBreakerConfig)
        assert cfg.ancestry_tracking is True

    def test_custom_values(self) -> None:
        cfg = LoopPreventionConfig(
            max_delegation_depth=10,
            rate_limit=RateLimitConfig(max_per_pair_per_minute=20),
            dedup_window_seconds=120,
            circuit_breaker=CircuitBreakerConfig(bounce_threshold=5),
        )
        assert cfg.max_delegation_depth == 10
        assert cfg.rate_limit.max_per_pair_per_minute == 20
        assert cfg.dedup_window_seconds == 120
        assert cfg.circuit_breaker.bounce_threshold == 5


@pytest.mark.unit
class TestLoopPreventionConfigValidation:
    def test_ancestry_tracking_false_rejected(self) -> None:
        with pytest.raises(
            ValidationError,
            match="Input should be True",
        ):
            LoopPreventionConfig(ancestry_tracking=False)  # type: ignore[arg-type]

    def test_zero_delegation_depth_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoopPreventionConfig(max_delegation_depth=0)

    def test_zero_dedup_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LoopPreventionConfig(dedup_window_seconds=0)


@pytest.mark.unit
class TestLoopPreventionConfigImmutability:
    def test_frozen(self) -> None:
        cfg = LoopPreventionConfig()
        with pytest.raises(ValidationError):
            cfg.max_delegation_depth = 10  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = LoopPreventionConfig()
        updated = original.model_copy(update={"max_delegation_depth": 10})
        assert updated.max_delegation_depth == 10
        assert original.max_delegation_depth == 5


@pytest.mark.unit
class TestLoopPreventionConfigSerialization:
    def test_json_roundtrip(self) -> None:
        cfg = LoopPreventionConfig(
            max_delegation_depth=8,
            dedup_window_seconds=90,
        )
        restored = LoopPreventionConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import LoopPreventionConfigFactory

        cfg = LoopPreventionConfigFactory.build()
        assert isinstance(cfg, LoopPreventionConfig)


# ── CommunicationConfig ────────────────────────────────────────


@pytest.mark.unit
class TestCommunicationConfigDefaults:
    def test_defaults(self) -> None:
        cfg = CommunicationConfig()
        assert cfg.default_pattern is CommunicationPattern.HYBRID
        assert isinstance(cfg.message_bus, MessageBusConfig)
        assert isinstance(cfg.meetings, MeetingsConfig)
        assert isinstance(cfg.hierarchy, HierarchyConfig)
        assert isinstance(cfg.loop_prevention, LoopPreventionConfig)

    def test_custom_values(self) -> None:
        cfg = CommunicationConfig(
            default_pattern=CommunicationPattern.EVENT_DRIVEN,
            message_bus=MessageBusConfig(backend=MessageBusBackend.REDIS),
            hierarchy=HierarchyConfig(allow_skip_level=True),
        )
        assert cfg.default_pattern is CommunicationPattern.EVENT_DRIVEN
        assert cfg.message_bus.backend is MessageBusBackend.REDIS
        assert cfg.hierarchy.allow_skip_level is True


@pytest.mark.unit
class TestCommunicationConfigImmutability:
    def test_frozen(self) -> None:
        cfg = CommunicationConfig()
        with pytest.raises(ValidationError):
            cfg.default_pattern = CommunicationPattern.HIERARCHICAL  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = CommunicationConfig()
        updated = original.model_copy(
            update={"default_pattern": CommunicationPattern.HIERARCHICAL}
        )
        assert updated.default_pattern is CommunicationPattern.HIERARCHICAL
        assert original.default_pattern is CommunicationPattern.HYBRID


@pytest.mark.unit
class TestCommunicationConfigSerialization:
    def test_json_roundtrip(self) -> None:
        cfg = CommunicationConfig(
            default_pattern=CommunicationPattern.MEETING_BASED,
            message_bus=MessageBusConfig(backend=MessageBusBackend.KAFKA),
        )
        restored = CommunicationConfig.model_validate_json(cfg.model_dump_json())
        assert restored == cfg

    def test_model_dump_enum_values(self) -> None:
        cfg = CommunicationConfig()
        dumped = cfg.model_dump()
        assert dumped["default_pattern"] == "hybrid"
        assert dumped["message_bus"]["backend"] == "internal"

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import CommunicationConfigFactory

        cfg = CommunicationConfigFactory.build()
        assert isinstance(cfg, CommunicationConfig)


@pytest.mark.unit
class TestCommunicationConfigFixtures:
    def test_sample_communication_config(
        self, sample_communication_config: CommunicationConfig
    ) -> None:
        expected = CommunicationPattern.HYBRID
        assert sample_communication_config.default_pattern is expected

    def test_sample_meeting_type(self, sample_meeting_type: MeetingTypeConfig) -> None:
        assert sample_meeting_type.name == "daily_standup"
        assert sample_meeting_type.frequency == "per_sprint_day"

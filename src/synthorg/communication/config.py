"""Communication configuration models (see Communication design page)."""

from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.conflict_resolution.config import (
    ConflictResolutionConfig,
)
from synthorg.communication.enums import (
    CommunicationPattern,
    MessageBusBackend,
)
from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.frequency import MeetingFrequency  # noqa: TC001
from synthorg.core.types import (
    NotBlankStr,
    validate_unique_strings,
)

# Default channels from the Communication design page.
_DEFAULT_CHANNELS: tuple[str, ...] = (
    "#all-hands",
    "#engineering",
    "#product",
    "#design",
    "#incidents",
    "#code-review",
    "#watercooler",
)


class MessageRetentionConfig(BaseModel):
    """Retention settings for channel message history.

    Attributes:
        max_messages_per_channel: Maximum messages kept per channel.
    """

    model_config = ConfigDict(frozen=True)

    max_messages_per_channel: int = Field(
        default=1000,
        gt=0,
        description="Maximum messages kept per channel",
    )


class MessageBusConfig(BaseModel):
    """Message bus backend configuration.

    Maps to the Communication design page ``message_bus``.

    Attributes:
        backend: Transport backend to use.
        channels: Pre-defined channel names.
        retention: Message retention settings.
    """

    model_config = ConfigDict(frozen=True)

    backend: MessageBusBackend = Field(
        default=MessageBusBackend.INTERNAL,
        description="Transport backend",
    )
    channels: tuple[NotBlankStr, ...] = Field(
        default=_DEFAULT_CHANNELS,
        description="Pre-defined channel names",
    )
    retention: MessageRetentionConfig = Field(
        default_factory=MessageRetentionConfig,
        description="Message retention settings",
    )

    @model_validator(mode="after")
    def _validate_channels(self) -> Self:
        """Ensure channel names are unique."""
        validate_unique_strings(self.channels, "channels")
        return self


class MeetingTypeConfig(BaseModel):
    """Configuration for a single meeting type.

    Maps to the Communication design page ``meetings.types[]``.  Exactly one of
    ``frequency`` or ``trigger`` must be set.

    Attributes:
        name: Meeting type name (e.g. ``"daily_standup"``).
        frequency: Recurrence schedule (mutually exclusive with trigger).
        trigger: Event trigger (mutually exclusive with frequency).
        participants: Participant role or agent identifiers.
        duration_tokens: Token budget for the meeting.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Meeting type name")
    frequency: MeetingFrequency | None = Field(
        default=None,
        description="Recurrence schedule",
    )
    trigger: NotBlankStr | None = Field(
        default=None,
        description="Event trigger",
    )
    participants: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Participant role or agent identifiers",
    )
    duration_tokens: int = Field(
        default=2000,
        gt=0,
        description="Token budget for the meeting",
    )
    protocol_config: MeetingProtocolConfig = Field(
        default_factory=MeetingProtocolConfig,
        description="Meeting protocol configuration",
    )

    @model_validator(mode="after")
    def _validate_frequency_or_trigger(self) -> Self:
        """Exactly one of frequency or trigger must be set."""
        if self.frequency is not None and self.trigger is not None:
            msg = "Only one of frequency or trigger may be set, not both"
            raise ValueError(msg)
        if self.frequency is None and self.trigger is None:
            msg = "Exactly one of frequency or trigger must be set"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_participants(self) -> Self:
        """Ensure participant entries are unique."""
        validate_unique_strings(self.participants, "participants")
        return self


class MeetingsConfig(BaseModel):
    """Meetings subsystem configuration.

    Maps to the Communication design page ``meetings``.

    Attributes:
        enabled: Whether the meetings subsystem is active.
        types: Configured meeting types (unique by name).
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Meetings subsystem active")
    types: tuple[MeetingTypeConfig, ...] = Field(
        default=(),
        description="Configured meeting types",
    )

    @model_validator(mode="after")
    def _validate_unique_meeting_names(self) -> Self:
        """Ensure meeting type names are unique."""
        names = [mt.name for mt in self.types]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate meeting type names: {dupes}"
            raise ValueError(msg)
        return self


class HierarchyConfig(BaseModel):
    """Hierarchy enforcement configuration.

    Maps to the Communication design page ``hierarchy``.

    Attributes:
        enforce_chain_of_command: Whether chain-of-command is enforced.
        allow_skip_level: Whether skip-level messaging is allowed.
    """

    model_config = ConfigDict(frozen=True)

    enforce_chain_of_command: bool = Field(
        default=True,
        description="Enforce chain-of-command",
    )
    allow_skip_level: bool = Field(
        default=False,
        description="Allow skip-level messaging",
    )


class RateLimitConfig(BaseModel):
    """Per-pair message rate limit configuration.

    Maps to the Communication design page ``rate_limit``.

    Attributes:
        max_per_pair_per_minute: Maximum messages per agent pair per minute.
        burst_allowance: Extra burst capacity above the rate limit.
    """

    model_config = ConfigDict(frozen=True)

    max_per_pair_per_minute: int = Field(
        default=10,
        gt=0,
        description="Max messages per agent pair per minute",
    )
    burst_allowance: int = Field(
        default=3,
        ge=0,
        description="Extra burst capacity",
    )


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration for agent-pair communication.

    Maps to the Communication design page ``circuit_breaker``.

    Attributes:
        bounce_threshold: Bounce count before the circuit opens.
        cooldown_seconds: Seconds to wait before retrying after trip.
    """

    model_config = ConfigDict(frozen=True)

    bounce_threshold: int = Field(
        default=3,
        gt=0,
        description="Bounce count before circuit opens",
    )
    cooldown_seconds: int = Field(
        default=300,
        gt=0,
        description="Cooldown period in seconds",
    )


class LoopPreventionConfig(BaseModel):
    """Loop prevention safeguards.

    Maps to the Communication design page.  ``ancestry_tracking`` is always on
    and cannot be disabled.

    Attributes:
        max_delegation_depth: Hard limit on delegation chain length.
        rate_limit: Per-pair rate limit settings.
        dedup_window_seconds: Deduplication window in seconds.
        circuit_breaker: Circuit breaker settings.
        ancestry_tracking: Must always be ``True``.
    """

    model_config = ConfigDict(frozen=True)

    max_delegation_depth: int = Field(
        default=5,
        gt=0,
        description="Hard limit on delegation chain length",
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig,
        description="Per-pair rate limit settings",
    )
    dedup_window_seconds: int = Field(
        default=60,
        gt=0,
        description="Deduplication window in seconds",
    )
    circuit_breaker: CircuitBreakerConfig = Field(
        default_factory=CircuitBreakerConfig,
        description="Circuit breaker settings",
    )
    ancestry_tracking: Literal[True] = Field(
        default=True,
        description="Task ancestry tracking (always on, not configurable)",
    )


class CommunicationConfig(BaseModel):
    """Top-level communication configuration.

    Aggregates the Communication design page sections under a single model.

    Attributes:
        default_pattern: High-level communication pattern.
        message_bus: Message bus configuration.
        meetings: Meetings subsystem configuration.
        hierarchy: Hierarchy enforcement settings.
        loop_prevention: Loop prevention safeguards.
    """

    model_config = ConfigDict(frozen=True)

    default_pattern: CommunicationPattern = Field(
        default=CommunicationPattern.HYBRID,
        description="High-level communication pattern",
    )
    message_bus: MessageBusConfig = Field(
        default_factory=MessageBusConfig,
        description="Message bus configuration",
    )
    meetings: MeetingsConfig = Field(
        default_factory=MeetingsConfig,
        description="Meetings subsystem configuration",
    )
    hierarchy: HierarchyConfig = Field(
        default_factory=HierarchyConfig,
        description="Hierarchy enforcement settings",
    )
    loop_prevention: LoopPreventionConfig = Field(
        default_factory=LoopPreventionConfig,
        description="Loop prevention safeguards",
    )
    conflict_resolution: ConflictResolutionConfig = Field(
        default_factory=ConflictResolutionConfig,
        description="Conflict resolution configuration (see Communication design page)",
    )

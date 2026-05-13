"""Communication configuration models (see Communication design page)."""

from collections import Counter
from typing import Final, Literal, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
from synthorg.observability import safe_error_description

_VALID_NATS_URL_SCHEMES: frozenset[str] = frozenset({"nats", "tls", "nats+tls"})
"""NATS URL schemes accepted at config load.

Matches the Go CLI's ``validateNatsURL`` allow-list in
``cli/cmd/worker_start.go`` so the config and the CLI enforce the
same rule at their respective system boundaries.
"""

_MIN_TCP_PORT: Final[int] = 1
_MAX_TCP_PORT: Final[int] = 65535
"""Legal TCP port range applied to ``NatsConfig.url`` at load time."""

# Default channels from the Communication design page. ``#settings`` is
# deliberately NOT included: ``SettingsChangeDispatcher._ensure_channel``
# owns its lifecycle and creates it on dispatcher start. Including it
# here would race the dispatcher and surface a benign
# ``communication.channel.already_exists`` WARNING on every boot.
_DEFAULT_CHANNELS: tuple[str, ...] = (
    "#all-hands",
    "#engineering",
    "#product",
    "#design",
    "#incidents",
    "#code-review",
    "#watercooler",
)


_MAX_SUBSCRIBER_QUEUE_SIZE_LIMIT: Final[int] = 65_535
"""Upper bound on subscriber queue sizes.

Chosen to match the typical operator-sane ceiling for JetStream
``max_ack_pending`` and to keep single-subscriber memory bounded
(``~65k envelopes * sizeof(DeliveryEnvelope)`` is tens of MB, not GB).
Guards against a misconfiguration or untrusted-config-source DoS
where an absurd value would exhaust memory at queue creation.
"""


class MessageRetentionConfig(BaseModel):
    """Retention settings for channel message history and subscriber delivery.

    Attributes:
        max_messages_per_channel: Maximum messages kept per channel
            for both in-memory and NATS backends. In-memory backend:
            ``collections.deque(maxlen=...)``. NATS backend:
            ``max_msgs_per_subject`` on the JetStream stream.
        max_subscriber_queue_size: Maximum in-flight envelopes per
            (channel, subscriber). In-memory backend applies a
            drop-newest policy with ``COMM_SUBSCRIBER_QUEUE_OVERFLOW``
            emission when the cap is hit. NATS backend wires this
            value into ``ConsumerConfig.max_ack_pending`` so JetStream
            pauses delivery to a consumer whose unacked count reaches
            the cap. The same configuration surface keeps the two
            backends at parity. Bounded above by
            :data:`_MAX_SUBSCRIBER_QUEUE_SIZE_LIMIT` to guard against
            a misconfiguration or untrusted-config-source DoS
            exhausting memory at queue creation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_messages_per_channel: int = Field(
        default=1000,
        gt=0,
        description="Maximum messages kept per channel",
    )
    max_subscriber_queue_size: int = Field(
        default=1024,
        gt=0,
        le=_MAX_SUBSCRIBER_QUEUE_SIZE_LIMIT,
        description=(
            "Maximum in-flight envelopes per (channel, subscriber). "
            "In-memory bus: hard cap with drop-newest policy. "
            "NATS: ConsumerConfig.max_ack_pending."
        ),
    )


class NatsConfig(BaseModel):
    """NATS JetStream backend configuration.

    Only applicable when ``MessageBusConfig.backend == NATS``. See
    ``docs/design/distributed-runtime.md`` for stream layout and
    subject naming.

    Attributes:
        url: NATS server URL (e.g. ``nats://localhost:4222``).
        credentials_path: Optional path to a credentials file for
            secured clusters (creds file or jwt+seed).
        stream_name_prefix: Prefix for JetStream stream names. The
            bus stream is ``<prefix>_BUS`` and the KV bucket for
            dynamic channels is ``<prefix>_BUS_CHANNELS``.
        connect_timeout_seconds: Seconds to wait for the initial
            connection before raising.
        reconnect_time_wait_seconds: Seconds between reconnect
            attempts.
        max_reconnect_attempts: Maximum reconnect attempts before
            giving up (``-1`` for unlimited).
        publish_ack_wait_seconds: Seconds to wait for a JetStream
            publish ack before considering the publish failed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Default points at a local-loopback NATS dev server. In-container
    # deployments override via ``SYNTHORG_NATS_URL`` (read by
    # ``workers/__main__.py``), or via the Go CLI's
    # ``SYNTHORG_DEFAULT_NATS_URL`` which targets the docker-compose
    # internal DNS name ``nats`` on port 4222.
    url: NotBlankStr = Field(
        default="nats://localhost:4222",
        description="NATS server URL",
    )

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        """Reject bad NATS URLs at config load instead of first connect.

        The in-process NATS client accepts "anything non-empty" and only
        fails later when it tries to dial the server, which leads to
        confusing errors downstream. Parse the URL here and require a
        recognised scheme, a non-empty host, and (if a port is present)
        a numeric port inside the legal TCP range so misconfiguration
        surfaces immediately at config load.
        """
        try:
            parsed = urlparse(value)
        except ValueError as exc:
            msg = f"invalid NATS url {value!r}: {safe_error_description(exc)}"
            raise ValueError(msg) from exc
        if parsed.scheme.lower() not in _VALID_NATS_URL_SCHEMES:
            schemes = ", ".join(sorted(_VALID_NATS_URL_SCHEMES))
            msg = (
                f"invalid NATS url {value!r}: scheme must be one of {schemes}; "
                f"got {parsed.scheme!r}"
            )
            raise ValueError(msg)
        if not parsed.hostname:
            msg = f"invalid NATS url {value!r}: missing host"
            raise ValueError(msg)
        # parsed.port raises ValueError for a non-numeric or negative
        # port; re-wrap with a contextual message. When no port is
        # present parsed.port returns None, which is fine (the client
        # uses the NATS default).
        try:
            port = parsed.port
        except ValueError as exc:
            msg = f"invalid NATS url {value!r}: non-numeric port in netloc"
            raise ValueError(msg) from exc
        if port is not None and not (_MIN_TCP_PORT <= port <= _MAX_TCP_PORT):
            msg = (
                f"invalid NATS url {value!r}: port {port} out of range "
                f"(must be {_MIN_TCP_PORT}-{_MAX_TCP_PORT})"
            )
            raise ValueError(msg)
        return value

    credentials_path: str | None = Field(
        default=None,
        description="Optional credentials file path",
    )
    stream_name_prefix: NotBlankStr = Field(
        default="SYNTHORG",
        description="Prefix for JetStream stream names",
    )
    connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Initial connect timeout",
    )
    reconnect_time_wait_seconds: float = Field(
        default=2.0,
        gt=0,
        description="Seconds between reconnect attempts",
    )
    max_reconnect_attempts: int = Field(
        default=-1,
        ge=-1,
        description="Max reconnect attempts (-1 = unlimited)",
    )
    publish_ack_wait_seconds: float = Field(
        default=5.0,
        gt=0,
        description="JetStream publish ack wait",
    )
    health_flush_timeout_seconds: float = Field(
        default=2.0,
        gt=0.0,
        le=30.0,
        description=(
            "Timeout in seconds for the NATS health-check flush probe. "
            "Deliberately tight so a stuck probe fails fast rather than "
            "blocking the ``/healthz`` endpoint."
        ),
    )


class MessageBusConfig(BaseModel):
    """Message bus backend configuration.

    Maps to the Communication design page ``message_bus``.

    Attributes:
        backend: Transport backend to use.
        channels: Pre-defined channel names.
        retention: Message retention settings.
        nats: NATS-specific configuration (required when
            ``backend == NATS``, ignored otherwise).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    nats: NatsConfig | None = Field(
        default=None,
        description="NATS-specific configuration (required when backend=nats)",
    )

    @model_validator(mode="after")
    def _validate_channels(self) -> Self:
        """Ensure channel names are unique."""
        validate_unique_strings(self.channels, "channels")
        return self

    @model_validator(mode="after")
    def _validate_backend_config(self) -> Self:
        """Ensure backend-specific config is provided when required."""
        if self.backend == MessageBusBackend.NATS and self.nats is None:
            msg = "message_bus.nats must be provided when message_bus.backend is 'nats'"
            raise ValueError(msg)
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    min_interval_seconds: int | None = Field(
        default=None,
        ge=1,
        description="Minimum seconds between event-triggered meetings of this type",
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
        if self.min_interval_seconds is not None and self.trigger is None:
            msg = "min_interval_seconds requires trigger-based meetings"
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    max_cooldown_seconds: int = Field(
        default=3600,
        gt=0,
        description="Maximum cooldown period in seconds (caps exponential backoff)",
    )

    @model_validator(mode="after")
    def _validate_cooldown_bounds(self) -> Self:
        """Ensure the exponential backoff cap is not below the base cooldown."""
        if self.max_cooldown_seconds < self.cooldown_seconds:
            msg = "max_cooldown_seconds must be >= cooldown_seconds"
            raise ValueError(msg)
        return self


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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

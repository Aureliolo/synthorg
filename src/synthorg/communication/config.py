"""Communication configuration models (see Communication design page)."""

from typing import ClassVar, Final, Self
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.communication.enums import (
    CommunicationPattern,
    MessageBusBackend,
    QuadraticEnforcementStrategy,
)
from synthorg.core.types import (
    NotBlankStr,
    validate_unique_strings,
)
from synthorg.observability import safe_error_description
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
)

_VALID_NATS_URL_SCHEMES: frozenset[str] = frozenset({"nats", "tls", "nats+tls"})
"""NATS URL schemes accepted at config load.

Matches the Go CLI's ``validateNatsURL`` allow-list in
``cli/cmd/worker_start.go`` so the config and the CLI enforce the
same rule at their respective system boundaries.
"""

_MIN_TCP_PORT: Final[int] = 1
_MAX_TCP_PORT: Final[int] = 65535
"""Legal TCP port range applied to ``NatsConfig.url`` at load time."""

# #settings excluded: SettingsChangeDispatcher._ensure_channel owns its lifecycle.
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

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="url",
            namespace=SettingNamespace.COMMUNICATION,
            key="nats_url",
            only_if_env_set=True,
        ),
    )

    # Default matches the registered ``communication.nats_url`` setting
    # (the worker entry point resolves the same key), i.e. the
    # docker-compose internal DNS name ``nats`` on port 4222. The
    # ``SYNTHORG_NATS_URL`` env override flows through the mirror; a
    # local-from-source run that has no NATS on that DNS name sets the
    # env var to its own URL.
    url: NotBlankStr = Field(
        default="nats://nats:4222",
        description="NATS server URL",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        """Overlay setting-namespace mirrors onto the raw input.

        Returns:
            The input data with mirrored settings applied.
        """
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)

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

        Returns:
            The validated NATS URL unchanged.

        Raises:
            ValueError: If the URL is unparseable, uses an unrecognised
                scheme, lacks a host, or has an out-of-range port.
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


class QuadraticEnforcementConfig(BaseModel):
    """O(n^2) message-overhead enforcement settings for the message bus.

    Detection compares the windowed inter-agent message count against
    ``team_size^2 * quadratic_threshold``.  The ``strategy`` decides
    what happens when that threshold is crossed (see
    :class:`~synthorg.communication.enums.QuadraticEnforcementStrategy`).

    Attributes:
        strategy: Enforcement mode (default ``alert_only``).
        quadratic_threshold: Fraction of ``team_size^2`` that marks a
            window as quadratic.
        window_seconds: Sliding window over which inter-agent publishes
            are counted.
        max_agent_connections: Participant ceiling enforced under
            ``hard_block``; admitting another agent past this is rejected.
        throttle_delay_seconds: Publish backpressure delay applied under
            ``soft_throttle`` while a window is quadratic.
        min_team_size: Smallest team for which detection runs; below this
            the quadratic comparison is noise and is skipped.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: QuadraticEnforcementStrategy = Field(
        default=QuadraticEnforcementStrategy.ALERT_ONLY,
        description="Quadratic enforcement mode",
    )
    quadratic_threshold: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description="Fraction of team_size^2 that marks a window quadratic",
    )
    window_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Sliding window for counting inter-agent publishes",
    )
    max_agent_connections: int = Field(
        default=50,
        gt=0,
        description="Participant ceiling enforced under hard_block",
    )
    throttle_delay_seconds: float = Field(
        default=0.05,
        ge=0.0,
        le=5.0,
        description="Publish backpressure delay under soft_throttle",
    )
    min_team_size: int = Field(
        default=3,
        gt=0,
        description="Smallest team for which quadratic detection runs",
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
        quadratic_enforcement: O(n^2) message-overhead enforcement.
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
    quadratic_enforcement: QuadraticEnforcementConfig = Field(
        default_factory=QuadraticEnforcementConfig,
        description="O(n^2) message-overhead enforcement settings",
    )

    @model_validator(mode="after")
    def _validate_channels(self) -> Self:
        """Ensure channel names are unique.

        Returns:
            The validated config.
        """
        validate_unique_strings(self.channels, "channels")
        return self

    @model_validator(mode="after")
    def _validate_backend_config(self) -> Self:
        """Ensure backend-specific config is provided when required.

        Returns:
            The validated config.

        Raises:
            ValueError: If ``backend`` is NATS but no ``nats`` config is
                set.
        """
        if self.backend == MessageBusBackend.NATS and self.nats is None:
            msg = "message_bus.nats must be provided when message_bus.backend is 'nats'"
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


class CommunicationConfig(BaseModel):
    """Top-level communication configuration.

    Aggregates the Communication design page sections under a single model.

    Attributes:
        default_pattern: High-level communication pattern.
        message_bus: Message bus configuration.
        hierarchy: Hierarchy enforcement settings.
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
    hierarchy: HierarchyConfig = Field(
        default_factory=HierarchyConfig,
        description="Hierarchy enforcement settings",
    )

"""Distributed task queue configuration.

Part of the Distributed Runtime design (see
``docs/design/distributed-runtime.md``). Opt-in: ``enabled=False`` by
default, and when set to ``True`` the message bus backend must be
distributed (not ``internal``).
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr

_NATS_FORBIDDEN_CHARS: frozenset[str] = frozenset({"*", ">", " ", "\t", "\n", "\r"})
"""Characters rejected in JetStream stream and subject tokens.

`*` and `>` are NATS wildcards that would match unrelated subjects.
Whitespace characters are not legal inside a token and lead to
hard-to-diagnose subscribe/publish failures at runtime.
"""


def _reject_nats_tokens(value: str, field_name: str) -> str:
    """Reject values that JetStream stream/subject configs cannot accept.

    Applied both to stream names (no dots, no wildcards) and to subject
    prefixes (dot-separated, non-empty tokens, no wildcards). Raises
    ``ValueError`` with a concrete diagnostic so config load fails fast
    at the system boundary instead of at ``pull_subscribe`` time.

    Returns:
        The validated value, unchanged.

    Raises:
        ValueError: When the value has surrounding whitespace or contains
            a NATS-forbidden character (``*``, ``>``, whitespace).
    """
    stripped = value.strip()
    if stripped != value:
        msg = f"{field_name} must not contain leading or trailing whitespace"
        raise ValueError(msg)
    for ch in _NATS_FORBIDDEN_CHARS:
        if ch in value:
            msg = (
                f"{field_name}={value!r} contains the forbidden character {ch!r}; "
                "NATS wildcards (`*`, `>`) and whitespace are not allowed in "
                "stream names or subject tokens"
            )
            raise ValueError(msg)
    return value


def _reject_nats_subject(value: str, field_name: str) -> str:
    """Validate a dot-separated NATS subject prefix.

    Returns:
        The validated subject prefix, unchanged.

    Raises:
        ValueError: When the prefix has a forbidden character or an empty
            dot-separated token.
    """
    _reject_nats_tokens(value, field_name)
    tokens = value.split(".")
    if any(token == "" for token in tokens):
        msg = (
            f"{field_name}={value!r} contains an empty token; NATS subject "
            "prefixes must be non-empty dot-separated tokens"
        )
        raise ValueError(msg)
    return value


class QueueConfig(BaseModel):
    """Distributed task queue configuration.

    When ``enabled`` is ``True``, the task engine registers a
    :class:`DistributedDispatcher` observer that publishes ready tasks
    to a JetStream work-queue stream. Workers (``synthorg worker
    start``) pull claims from the stream and execute tasks via the
    backend HTTP API.

    Attributes:
        enabled: Whether the distributed queue is active. Default
            ``False`` (in-process dispatch only).
        stream_name: JetStream stream name for the work queue.
        ready_subject_prefix: Subject prefix for claim messages.
            Full subject is ``<prefix>.<task_id>``.
        dead_subject_prefix: Subject prefix for dead-letter messages.
        workers: Default worker count for ``synthorg worker start``.
        ack_wait_seconds: JetStream ack deadline. Workers must ack
            within this many seconds or the message is redelivered.
        max_deliver: Maximum redelivery attempts before a claim is
            routed to the dead-letter subject.
        heartbeat_interval_seconds: Seconds between worker heartbeat
            publications. Also reused as the working-ack cadence: the
            worker calls ``in_progress`` on this interval while the
            executor runs so a long task cannot exceed ``ack_wait`` and
            trigger a duplicate redelivery. Must stay well below
            ``ack_wait_seconds``.
        max_ack_pending: Upper bound on unacknowledged claims in flight
            across the shared durable consumer. The real backpressure
            lever: JetStream stops delivering once this many claims are
            outstanding, so a slow worker pool throttles intake instead
            of letting unbounded work pile into memory.
        stream_max_msgs: Hard cap on messages retained in the
            ``WorkQueuePolicy`` stream. Bounds disk growth if dispatch
            outpaces drain; the oldest is discarded past the cap.
        stream_max_bytes: Hard cap on stream size in bytes. Companion
            to ``stream_max_msgs`` for byte-bounded backpressure.
        prune_interval_seconds: Interval at which the backend prunes
            expired ``seen_claims`` dedup rows so the table cannot grow
            without bound.
        api_url: Backend HTTP API URL that workers call to transition
            tasks. ``None`` means "derive from env at runtime".
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether the distributed queue is active",
    )
    stream_name: NotBlankStr = Field(
        default="SYNTHORG_TASKS",
        description="JetStream stream name for the work queue",
    )
    ready_subject_prefix: NotBlankStr = Field(
        default="synthorg.tasks.ready",
        description="Subject prefix for claim messages",
    )
    dead_subject_prefix: NotBlankStr = Field(
        default="synthorg.tasks.dead",
        description="Subject prefix for dead-letter messages",
    )
    workers: int = Field(
        default=4,
        gt=0,
        description="Default worker count",
    )
    ack_wait_seconds: int = Field(
        default=300,
        gt=0,
        description="JetStream ack deadline in seconds",
    )
    max_deliver: int = Field(
        default=3,
        gt=0,
        description="Max redelivery attempts before DLQ",
    )
    heartbeat_interval_seconds: int = Field(
        default=30,
        gt=0,
        description="Seconds between worker heartbeats / ack-extension cadence",
    )
    max_ack_pending: int = Field(
        default=16,
        gt=0,
        description="Max unacked claims in flight across the shared consumer",
    )
    stream_max_msgs: int = Field(
        default=100_000,
        gt=0,
        description="Max messages retained in the work-queue stream",
    )
    stream_max_bytes: int = Field(
        default=1_073_741_824,
        gt=0,
        description="Max work-queue stream size in bytes (1 GiB default)",
    )
    prune_interval_seconds: int = Field(
        default=3600,
        gt=0,
        description="Interval between seen_claims dedup-row prunes",
    )
    api_url: str | None = Field(
        default=None,
        description="Backend HTTP API URL for task transitions",
    )

    @field_validator("stream_name")
    @classmethod
    def _validate_stream_name(cls, value: str) -> str:
        """Reject wildcards, whitespace and dots inside the stream name.

        JetStream stream names are a single token (no dots), so reuse
        the shared token validator but additionally reject ``.`` to
        prevent config drift between "stream name" and "subject prefix".

        Returns:
            The validated stream name, unchanged.

        Raises:
            ValueError: When the name has a forbidden character or a
                ``.``.
        """
        _reject_nats_tokens(value, "stream_name")
        if "." in value:
            msg = (
                f"stream_name={value!r} must not contain '.'; stream names are "
                "single tokens"
            )
            raise ValueError(msg)
        return value

    @field_validator("ready_subject_prefix")
    @classmethod
    def _validate_ready_subject_prefix(cls, value: str) -> str:
        """Reject wildcards/whitespace/empty tokens in the ready subject.

        Returns:
            The validated ready-subject prefix, unchanged.

        Raises:
            ValueError: When the prefix has a forbidden character or an
                empty dot-separated token.
        """
        return _reject_nats_subject(value, "ready_subject_prefix")

    @field_validator("dead_subject_prefix")
    @classmethod
    def _validate_dead_subject_prefix(cls, value: str) -> str:
        """Reject wildcards/whitespace/empty tokens in the dead-letter subject.

        Returns:
            The validated dead-subject prefix, unchanged.

        Raises:
            ValueError: When the prefix has a forbidden character or an
                empty dot-separated token.
        """
        return _reject_nats_subject(value, "dead_subject_prefix")

    @model_validator(mode="after")
    def _validate_subjects(self) -> Self:
        """Ensure ready and dead subjects do not overlap.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When the ready and dead subject prefixes are
                equal.
        """
        if self.ready_subject_prefix == self.dead_subject_prefix:
            msg = "ready_subject_prefix and dead_subject_prefix must differ"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_ack_extension_headroom(self) -> Self:
        """The working-ack cadence must stay below the ack deadline.

        ``heartbeat_interval_seconds`` doubles as the interval at which
        the worker calls ``in_progress`` while executing. If it were
        >= ``ack_wait_seconds`` the deadline could lapse before the
        first extension fires, defeating the no-duplication guarantee.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``heartbeat_interval_seconds`` is not less
                than ``ack_wait_seconds``.
        """
        if self.heartbeat_interval_seconds >= self.ack_wait_seconds:
            msg = (
                "heartbeat_interval_seconds must be < ack_wait_seconds so "
                "the working-ack extension fires before the ack deadline"
            )
            raise ValueError(msg)
        return self

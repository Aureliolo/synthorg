"""Escalation queue configuration."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Safe PostgreSQL unquoted-identifier pattern.  The notify subscriber
# embeds the channel name in a ``LISTEN "<channel>"`` statement, so the
# identifier must contain no double quotes or control characters that
# could break out of the quoting.  Keeping the charset strict (ASCII
# letter/digit/underscore, starting with a letter or underscore) is
# also friendly to every driver we care about.
_NOTIFY_CHANNEL_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
_NOTIFY_CHANNEL_MAX_LEN = 63  # PostgreSQL's NAMEDATALEN-1

NotifyChannel = Annotated[
    str,
    StringConstraints(
        pattern=re.compile(_NOTIFY_CHANNEL_PATTERN),
        min_length=1,
        max_length=_NOTIFY_CHANNEL_MAX_LEN,
    ),
]


class EscalationQueueConfig(BaseModel):
    """Pluggable configuration for the human escalation approval queue.

    Attributes:
        backend: Which :class:`EscalationQueueStore` strategy to use.
            ``memory`` is the default for tests / ephemeral deployments;
            ``sqlite`` and ``postgres`` use the corresponding persistence
            backends.
        decision_strategy: Which :class:`DecisionProcessor` strategy to
            use.  ``winner`` only accepts "pick a winning agent"
            decisions -- the safest default.  ``hybrid`` also accepts
            ``reject`` (abandon the conflict).
        default_timeout_seconds: Maximum seconds to await a human
            decision before the resolver gives up with an ``EXPIRED``
            outcome.  ``None`` means wait forever; operators opting in
            to "wait forever" must also arrange some manual cancellation
            workflow.
        sweeper_interval_seconds: How often the
            :class:`EscalationExpirationSweeper` runs.  Values below
            ``5`` are rejected to keep the background loop inexpensive.
        reconnect_delay_seconds: Delay before the cross-instance
            ``PostgresEscalationNotifySubscriber`` reconnects after
            a connection drop.  Tunable via the
            ``communication.escalation_subscriber_reconnect_delay_seconds``
            setting.
        cross_instance_notify: Whether the escalation queue should
            publish/subscribe state-change signals across workers.
            ``auto`` enables LISTEN/NOTIFY when ``backend == "postgres"``
            and is a no-op for ``memory``/``sqlite`` (which are
            single-process by definition).  ``on`` forces subscription
            and fails startup if the backend cannot support it.
            ``off`` scopes the feature to a single worker.
        notify_channel: Postgres LISTEN/NOTIFY channel name.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backend: Literal["memory", "sqlite", "postgres"] = "memory"
    decision_strategy: Literal["winner", "hybrid"] = "winner"
    default_timeout_seconds: int | None = Field(default=None, ge=1)
    sweeper_interval_seconds: float = Field(default=30.0, ge=5.0)
    reconnect_delay_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    cross_instance_notify: Literal["auto", "on", "off"] = "auto"
    notify_channel: NotifyChannel = Field(default="conflict_escalation_events")

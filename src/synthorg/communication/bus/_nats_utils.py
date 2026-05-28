"""Utility functions and constants for the NATS bus submodules.

Most functions in this module are pure (encoding, URL redaction,
task cancellation) and operate on primitive values or asyncio tasks.
The exception is :func:`require_running`, which accepts a
``_NatsState`` instance to validate bus liveness.

``redact_url`` is re-exported via ``synthorg.communication.bus`` for
use by external code such as ``workers/claim.py``.
"""

import asyncio
import base64
from typing import Any, Final, NoReturn
from urllib.parse import urlparse

from synthorg.communication.bus._nats_state import _NatsState
from synthorg.communication.errors import (
    ChannelNotFoundError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.communication import (
    COMM_BUS_NOT_RUNNING,
    COMM_BUS_RECEIVE_ERROR,
    COMM_CHANNEL_NOT_FOUND,
    COMM_SUBSCRIPTION_NOT_FOUND,
)

logger = get_logger(__name__)

# ---- constants --------------------------------------------------------

DM_SEPARATOR = ":"
"""Separator used in deterministic direct-channel names (matches in-memory)."""

SUBJECT_CHANNEL_TOKEN: Final[str] = "channel"  # noqa: S105
SUBJECT_DIRECT_TOKEN: Final[str] = "direct"  # noqa: S105

MAX_BUS_PAYLOAD_BYTES: Final[int] = 4 * 1024 * 1024
"""Maximum bus message payload size (4 MB) accepted from JetStream.

Messages include parts that can carry text/data blobs, so the limit
is higher than the task-claim limit but still bounded to prevent a
single malformed publisher from exhausting worker memory during
deserialization.
"""

RECEIVE_POLL_WINDOW_SECONDS: Final[float] = 60.0
"""Maximum seconds a single JetStream fetch waits before looping.

``receive()`` uses this value as the upper bound on a single
``_fetch_with_shutdown`` call. A ``timeout=None`` caller loops over
these polls until a message arrives or the bus shuts down; a
bounded ``timeout`` decrements the remaining budget by this window
each iteration. Keeps per-fetch server-side state bounded while
still matching the in-memory bus's "block indefinitely" contract.
"""

CONSUMER_ACK_WAIT_MULTIPLIER: Final[float] = 6.0
"""Multiplier on ``publish_ack_wait_seconds`` for per-subscriber consumer ack_wait.

A subscriber's durable pull consumer gets an ack deadline that is
several times longer than the publisher's ack wait: publish acks are a
server-side fire-and-forget acknowledgement, while the subscriber's
ack deadline must span receive + application processing + the
possibility of redelivery before being considered in-flight. The 6x
factor mirrors typical JetStream guidance for interactive workloads
and is surfaced here as a named constant so tests and operators can
reason about it without grepping for a raw literal.
"""


# ---- helpers ----------------------------------------------------------


def redact_url(url: str) -> str:
    """Strip credentials from a NATS URL for safe logging.

    ``nats://user:pass@host:port`` -> ``nats://***@host:port``.
    Non-URL strings pass through unchanged (best effort).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.hostname:
        return url
    authority = parsed.hostname
    if parsed.port is not None:
        authority = f"{authority}:{parsed.port}"
    has_creds = parsed.username is not None or parsed.password is not None
    if has_creds:
        authority = f"***@{authority}"
    scheme = parsed.scheme or "nats"
    rest = parsed.path or ""
    return f"{scheme}://{authority}{rest}"


def raise_channel_not_found(channel_name: str) -> NoReturn:
    """Log and raise :class:`ChannelNotFoundError`."""
    logger.warning(COMM_CHANNEL_NOT_FOUND, channel=channel_name)
    msg = f"Channel not found: {channel_name}"
    raise ChannelNotFoundError(msg, context={"channel": channel_name})


def raise_not_subscribed(
    channel_name: str,
    subscriber_id: str,
) -> NoReturn:
    """Log and raise :class:`NotSubscribedError`."""
    logger.warning(
        COMM_SUBSCRIPTION_NOT_FOUND,
        channel=channel_name,
        subscriber=subscriber_id,
    )
    msg = f"Not subscribed to {channel_name}"
    raise NotSubscribedError(
        msg,
        context={
            "channel": channel_name,
            "subscriber": subscriber_id,
        },
    )


async def cancel_if_pending(task: asyncio.Task[Any]) -> None:
    """Cancel a task, await completion, and suppress CancelledError.

    Any exception other than ``CancelledError`` is logged at WARNING
    and re-raised so the caller can decide whether recovery is
    possible.
    """
    if task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            COMM_BUS_RECEIVE_ERROR,
            phase="cancel_pending_task",
            task_repr=repr(task),
        )
        raise


def encode_token(name: str) -> str:
    """Encode an arbitrary string into a NATS-subject-safe token.

    JetStream subject tokens may contain alphanumerics, ``-``, and
    ``_`` but not ``#``, ``@``, ``:``, ``.`` or other separators used
    in SynthOrg channel names. Base32 (lowercase, no padding) gives a
    deterministic, collision-free, case-insensitive encoding using
    only safe characters.
    """
    raw = name.encode("utf-8")
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def decode_token(token: str) -> str:
    """Reverse of :func:`encode_token`."""
    padding = "=" * ((-len(token)) % 8)
    raw = base64.b32decode((token.upper() + padding).encode("ascii"))
    return raw.decode("utf-8")


def require_running(state: _NatsState) -> None:
    """Raise if the bus is not running."""
    if not state.running:
        logger.warning(COMM_BUS_NOT_RUNNING)
        msg = "Message bus is not running"
        raise MessageBusNotRunningError(msg)

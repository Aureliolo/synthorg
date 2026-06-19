"""Errors specific to distributed bus backends.

Generic ``MessageBus`` errors live in ``synthorg.communication.errors``
and are raised by both the in-memory and distributed backends. This
module holds errors that only make sense for distributed transports
(connection failures, stream setup failures, etc.).
"""

from typing import ClassVar

from synthorg.communication.errors import CommunicationError
from synthorg.core.domain_errors import ConflictError


class BusConnectionError(CommunicationError):
    """Raised when a distributed bus backend cannot connect to its transport.

    Signals a non-retryable failure at the transport layer, e.g. the
    NATS URL is unreachable or credentials are rejected. Callers that
    catch this should surface it as a fatal startup error.
    """


class BusStreamError(CommunicationError):
    """Raised when a distributed bus backend cannot set up or query a stream.

    Covers JetStream stream creation failures, durable consumer creation
    failures, and KV bucket setup failures. Context typically includes
    ``stream`` or ``bucket`` keys identifying the failing primitive.
    """


class BusStopTimeoutError(CommunicationError):
    """Raised when a bus / queue client's ``stop()`` drain exceeds its hard deadline.

    Per ``docs/reference/lifecycle-sync.md`` a timed-out drain must mark
    the instance unrestartable: the partially-drained NATS connection
    may still hold the durable consumer's pull subscription and a fresh
    ``start()`` would attach a second listener. The class flips its
    ``_stop_failed`` flag before raising; callers that catch this error
    must construct a fresh instance to recover.
    """


class BusUnrestartableError(ConflictError):
    """Raised when ``start()`` is called after a timed-out ``stop()``.

    See :class:`BusStopTimeoutError` for the underlying invariant. The
    only safe recovery is constructing a fresh instance, so this error
    surfaces the operator-visible signal rather than silently retrying.

    Subclasses :class:`ConflictError` (409 / ``RESOURCE_CONFLICT``) so it
    is consistent with the other lifecycle ``*UnrestartableError`` classes:
    an unrestartable instance is a lifecycle conflict, not an internal
    error, so an HTTP client must not auto-retry the ``start()`` on a 5xx.
    """

    default_message: ClassVar[str] = "Message bus is unrestartable"

"""Message bus backend package.

Exposes the concrete backends and a :func:`build_message_bus` factory
that picks the right implementation based on
``MessageBusConfig.backend``. Callers that need a specific backend
can import it directly.

See ``docs/design/distributed-runtime.md`` for the overall design.
"""

from synthorg.communication.bus._nats_utils import redact_url
from synthorg.communication.bus.memory import InMemoryMessageBus
from synthorg.communication.bus.quadratic_enforcement import QuadraticEnforcer
from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.config import MessageBusConfig
from synthorg.communication.enums import MessageBusBackend
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)

__all__ = (
    "InMemoryMessageBus",
    "MessageBus",
    "build_message_bus",
    "redact_url",
)


def build_message_bus(config: MessageBusConfig) -> MessageBus:
    """Build the concrete ``MessageBus`` for the given configuration.

    The factory pattern-matches on ``config.backend`` and returns the
    matching implementation. Optional dependencies for distributed
    backends are imported lazily so a default (internal) deployment
    does not need them installed.

    Args:
        config: Message bus configuration.

    Returns:
        A concrete ``MessageBus`` instance (not started).

    Raises:
        ImportError: If the selected backend requires an optional
            dependency that is not installed (e.g. ``nats-py`` for
            the NATS backend).
        ValueError: If ``config.backend`` is an unrecognised backend.
    """
    match config.backend:
        case MessageBusBackend.INTERNAL:
            enforcer = QuadraticEnforcer(config=config.quadratic_enforcement)
            return InMemoryMessageBus(config=config, quadratic_enforcer=enforcer)
        case MessageBusBackend.NATS:
            from synthorg.communication.bus.nats import (  # noqa: PLC0415
                JetStreamMessageBus,
            )

            return JetStreamMessageBus(config=config)
        case _:
            # Defensive catch-all: any future MessageBusBackend member
            # that is not wired up above should fail loudly at startup
            # rather than silently skipping auto-wire. Mypy sees this
            # branch as unreachable because the preceding cases exhaust
            # the enum, but the catch-all intentionally guards against
            # forgetting to update this factory when a new member is
            # added to MessageBusBackend.
            msg = (  # type: ignore[unreachable]
                f"Unknown MessageBus backend {config.backend!r}. "
                "The factory has not been updated for this enum member; "
                "add a new case to build_message_bus()."
            )
            logger.error(
                CONFIG_VALIDATION_FAILED,
                model="MessageBusConfig",
                backend=repr(config.backend),
                reason="unknown_backend_factory_not_updated",
            )
            raise ValueError(msg)
